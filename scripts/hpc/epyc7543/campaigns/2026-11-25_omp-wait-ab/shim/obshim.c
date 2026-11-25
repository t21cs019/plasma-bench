/*
 * obshim.c — OpenBLAS の blas_memory_alloc／blas_memory_free を LD_PRELOAD で包む（2026-11-24）
 *
 * 目的：
 *   1. 呼び出しの回数と1回あたりの時間（rdtsc）を、計時区間の中だけで数える（OBSHIM_MODE=pass）
 *   2. 共有の表 memory[] を通らない、スレッドごとの作業領域の使い回しに置き換える（OBSHIM_MODE=tls）
 *   3. plasmatest の計時（omp_get_wtime）の時刻を記録する。perf（-k CLOCK_MONOTONIC）と
 *      strace（-ttt＝CLOCK_REALTIME）の時刻を、計時区間にぴったり合わせて切るため
 *
 * 前提：libopenblas の中の blas_memory_alloc は PLT 経由で呼ばれる（0.3.28 で確認。0.3.30.dev は
 *       n_alloc が 0 でないことで確かめる）。plasmatest は omp_get_wtime を PLT 経由で呼ぶ。
 *
 * 環境変数：
 *   OBSHIM_MODE   pass（既定。本物を呼び、数えて測る）｜tls（置き換え）｜stamp（時刻だけ。数えない）
 *   OBSHIM_OUT    出力先のファイル（既定は標準エラー）。JSON を1行ずつ
 *   OBSHIM_BUFSZ  tls の作業領域1個の大きさ [MB]（既定 256。OpenBLAS の x86_64 の既定 BUFFER_SIZE は 128 MB）
 *
 * 区間（phase）：omp_get_wtime が k 回呼ばれた後の呼び出しは phase k に入る。
 *   plasmatest dgeqrf --test=n では start＝1回目、stop＝2回目なので、計時区間は phase 1。
 *   解析で、stamp の差が plasmatest の Time に一致する組を選んで確かめる。
 *
 * ビルド：gcc -O2 -fPIC -shared -o obshim.so obshim.c -ldl -lpthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <x86intrin.h>

#define MAXPH 8          /* 区間の数（それより後は最後の区間にまとめる） */
#define MAXTHR 1024
#define NHIST 40         /* log2(サイクル) のヒストグラム */
#define NDEPTH 256       /* 作業領域の通し番号（最初に見た順）のヒストグラム */
#define MAXSTAMP 64
#define HSIZE 8192       /* アドレス → 通し番号 の表 */
#define TCACHE 8
#define NCALLER 32       /* 呼び出し元（戻り先のアドレス）の表。スレッドごと */

typedef struct {
    uint64_t n_alloc, n_free, cyc_alloc, cyc_free;
    uint64_t max_alloc, max_free;
    uint64_t hist_alloc[NHIST], hist_free[NHIST];
    uint64_t hist_depth[NDEPTH];
} phstat_t;

typedef struct {
    phstat_t ph[MAXPH];
    void *cache[TCACHE];
    int ncache;
    uintptr_t caller[NCALLER];
    uint64_t caller_n[NCALLER];
    int tid;
} __attribute__((aligned(64))) thrstat_t;

typedef struct {
    double wtime;
    uint64_t mono_ns, real_ns, tsc;
    long minflt, majflt, nvcsw, nivcsw;
} stamp_t;

static void *(*real_alloc)(int);
static void (*real_free)(void *);
static double (*real_wtime)(void);

static int mode;                 /* 0 pass, 1 tls, 2 stamp */
static size_t bufsz;
static FILE *out;
static thrstat_t *threads[MAXTHR];
static atomic_int nthreads;
static stamp_t stamps[MAXSTAMP];
static atomic_int nstamp;
static uint64_t tsc0, mono0;
static __thread thrstat_t *me;

/* アドレス → 通し番号（最初に見た順）。挿入だけ。tls では自分の作業領域の印にも使う */
static _Atomic(uintptr_t) hkey[HSIZE];
static atomic_int hval[HSIZE];
static atomic_int nbuf;

static pthread_once_t once = PTHREAD_ONCE_INIT;
static pthread_mutex_t spill_mu = PTHREAD_MUTEX_INITIALIZER;
static void *spill[4096];
static int nspill;

static uint64_t now_ns(clockid_t c) {
    struct timespec ts;
    clock_gettime(c, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}

static int hfind(uintptr_t a, int insert) {
    unsigned h = (unsigned)((a >> 12) * 2654435761u) & (HSIZE - 1);
    for (int i = 0; i < HSIZE; i++, h = (h + 1) & (HSIZE - 1)) {
        uintptr_t k = atomic_load_explicit(&hkey[h], memory_order_acquire);
        if (k == a) {
            int v;
            while ((v = atomic_load(&hval[h])) == 0) _mm_pause();
            return v - 1;
        }
        if (k == 0) {
            if (!insert) return -1;
            uintptr_t z = 0;
            if (atomic_compare_exchange_strong(&hkey[h], &z, a)) {
                int v = atomic_fetch_add(&nbuf, 1);
                atomic_store(&hval[h], v + 1);
                return v;
            }
            if (z == a) { i--; continue; }  /* 同じアドレスを他のスレッドが入れた：読み直す */
        }
    }
    return -1;
}

static void init_syms(void) {
    real_alloc = (void *(*)(int))dlsym(RTLD_NEXT, "blas_memory_alloc");
    real_free = (void (*)(void *))dlsym(RTLD_NEXT, "blas_memory_free");
    real_wtime = (double (*)(void))dlsym(RTLD_NEXT, "omp_get_wtime");
}

static atomic_int inited;
/* libopenblas のコンストラクタ（gotoblas_init）が、この .so のコンストラクタより先に alloc を呼ぶので、
   最初の呼び出しで初期化する */
static void obshim_init(void) {
    int z = 0;
    if (!atomic_compare_exchange_strong(&inited, &z, 1)) { while (atomic_load(&inited) != 2) _mm_pause(); return; }
    const char *m = getenv("OBSHIM_MODE");
    mode = (m && !strcmp(m, "tls")) ? 1 : (m && !strcmp(m, "stamp")) ? 2 : 0;
    const char *b = getenv("OBSHIM_BUFSZ");
    bufsz = (size_t)(b ? atol(b) : 256) << 20;
    const char *o = getenv("OBSHIM_OUT");
    out = o ? fopen(o, "w") : NULL;
    if (!out) out = stderr;
    init_syms();
    tsc0 = __rdtsc();
    mono0 = now_ns(CLOCK_MONOTONIC);
    atomic_store(&inited, 2);
}
__attribute__((constructor)) static void obshim_ctor(void) { obshim_init(); }
#define ENSURE() do { if (atomic_load_explicit(&inited, memory_order_acquire) != 2) obshim_init(); } while (0)

static thrstat_t *self(void) {
    if (!me) {
        thrstat_t *t = aligned_alloc(64, sizeof(thrstat_t));
        memset(t, 0, sizeof(*t));
        t->tid = (int)syscall(SYS_gettid);   /* glibc 2.28（AOBA）には gettid() が無い */
        int i = atomic_fetch_add(&nthreads, 1);
        if (i < MAXTHR) threads[i] = t;
        me = t;
    }
    return me;
}

static inline int cur_phase(void) {
    int k = atomic_load_explicit(&nstamp, memory_order_relaxed);
    return k < MAXPH ? k : MAXPH - 1;
}

static inline int lg2(uint64_t c) {
    int b = c ? 63 - __builtin_clzll(c) : 0;
    return b < NHIST ? b : NHIST - 1;
}

/* tls：初回に本物を1回呼んで OpenBLAS の初期化（memory_initialized、DYNAMIC_ARCH）を済ませる */
static void warm_real(void) {
    if (!real_alloc) init_syms();
    void *p = real_alloc(0);
    real_free(p);
}

static void *tls_alloc(thrstat_t *t) {
    pthread_once(&once, warm_real);
    if (t->ncache > 0) return t->cache[--t->ncache];
    pthread_mutex_lock(&spill_mu);
    void *p = nspill > 0 ? spill[--nspill] : NULL;
    pthread_mutex_unlock(&spill_mu);
    if (p) return p;
    p = mmap(NULL, bufsz, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (p == MAP_FAILED) { perror("obshim mmap"); abort(); }
    hfind((uintptr_t)p, 1);   /* 自分の作業領域の印 */
    return p;
}

static int tls_free(thrstat_t *t, void *p) {
    if (hfind((uintptr_t)p, 0) < 0) return 0;   /* 本物が確保したもの */
    if (t->ncache < TCACHE) { t->cache[t->ncache++] = p; return 1; }
    pthread_mutex_lock(&spill_mu);
    if (nspill < 4096) spill[nspill++] = p;   /* munmap はしない（アドレスの再利用で印が狂うため） */
    pthread_mutex_unlock(&spill_mu);
    return 1;
}

void *blas_memory_alloc(int procpos) {
    ENSURE();
    if (mode == 2) return real_alloc(procpos);
    thrstat_t *t = self();
    int k = cur_phase();
    uint64_t c0 = __rdtsc();
    void *p = mode == 1 ? tls_alloc(t) : real_alloc(procpos);
    uint64_t c = __rdtsc() - c0;
    phstat_t *s = &t->ph[k];
    s->n_alloc++; s->cyc_alloc += c; s->hist_alloc[lg2(c)]++;
    if (c > s->max_alloc) s->max_alloc = c;
    {   /* 呼び出し元（区間 1 以降だけ数える） */
        uintptr_t ra = (uintptr_t)__builtin_return_address(0);
        if (k >= 1) for (int i = 0; i < NCALLER; i++) {
            if (t->caller[i] == ra) { t->caller_n[i]++; break; }
            if (t->caller[i] == 0) { t->caller[i] = ra; t->caller_n[i] = 1; break; }
        }
    }
    if (mode == 0) {
        int d = hfind((uintptr_t)p, 1);
        s->hist_depth[d < 0 ? NDEPTH - 1 : (d < NDEPTH ? d : NDEPTH - 1)]++;
    }
    return p;
}

void blas_memory_free(void *p) {
    ENSURE();
    if (mode == 2) { real_free(p); return; }
    thrstat_t *t = self();
    int k = cur_phase();
    uint64_t c0 = __rdtsc();
    if (!(mode == 1 && tls_free(t, p))) real_free(p);
    uint64_t c = __rdtsc() - c0;
    phstat_t *s = &t->ph[k];
    s->n_free++; s->cyc_free += c; s->hist_free[lg2(c)]++;
    if (c > s->max_free) s->max_free = c;
}

double omp_get_wtime(void) {
    ENSURE();
    double w = real_wtime ? real_wtime() : (double)now_ns(CLOCK_MONOTONIC) * 1e-9;
    int i = atomic_load(&nstamp);
    if (i < MAXSTAMP) {
        stamp_t *s = &stamps[i];
        struct rusage ru;
        getrusage(RUSAGE_SELF, &ru);
        s->wtime = w;
        s->tsc = __rdtsc();
        s->mono_ns = now_ns(CLOCK_MONOTONIC);
        s->real_ns = now_ns(CLOCK_REALTIME);
        s->minflt = ru.ru_minflt; s->majflt = ru.ru_majflt;
        s->nvcsw = ru.ru_nvcsw; s->nivcsw = ru.ru_nivcsw;
    }
    atomic_fetch_add(&nstamp, 1);   /* この後の呼び出しは次の区間 */
    return w;
}

static void put_arr(const char *name, const uint64_t *a, int n) {
    int last = -1;
    for (int i = 0; i < n; i++) if (a[i]) last = i;
    fprintf(out, ",\"%s\":[", name);
    for (int i = 0; i <= last; i++) fprintf(out, "%s%llu", i ? "," : "", (unsigned long long)a[i]);
    fprintf(out, "]");
}

__attribute__((destructor)) static void obshim_fini(void) {
    uint64_t tsc1 = __rdtsc(), mono1 = now_ns(CLOCK_MONOTONIC);
    double hz = (double)(tsc1 - tsc0) / ((double)(mono1 - mono0) * 1e-9);
    int nt = atomic_load(&nthreads); if (nt > MAXTHR) nt = MAXTHR;
    int ns = atomic_load(&nstamp);
    fprintf(out, "{\"type\":\"meta\",\"mode\":\"%s\",\"pid\":%d,\"tsc_hz\":%.0f,\"threads_seen\":%d,"
                 "\"stamps\":%d,\"distinct_buffers\":%d,\"bufsz_mb\":%zu,\"real_alloc_found\":%d}\n",
            mode == 1 ? "tls" : mode == 2 ? "stamp" : "pass", (int)getpid(), hz, nt, ns,
            atomic_load(&nbuf), bufsz >> 20, real_alloc != NULL);
    for (int i = 0; i < ns && i < MAXSTAMP; i++) {
        stamp_t *s = &stamps[i];
        fprintf(out, "{\"type\":\"stamp\",\"i\":%d,\"wtime\":%.9f,\"mono_ns\":%llu,\"real_ns\":%llu,\"tsc\":%llu,"
                     "\"minflt\":%ld,\"majflt\":%ld,\"nvcsw\":%ld,\"nivcsw\":%ld}\n",
                i, s->wtime, (unsigned long long)s->mono_ns, (unsigned long long)s->real_ns,
                (unsigned long long)s->tsc, s->minflt, s->majflt, s->nvcsw, s->nivcsw);
    }
    if (mode == 2) { fflush(out); return; }
    for (int k = 0; k < MAXPH; k++) {
        phstat_t sum; memset(&sum, 0, sizeof(sum));
        int active = 0; uint64_t nmax = 0, nmin = UINT64_MAX;
        for (int j = 0; j < nt; j++) {
            phstat_t *s = &threads[j]->ph[k];
            if (s->n_alloc) { active++; if (s->n_alloc > nmax) nmax = s->n_alloc; if (s->n_alloc < nmin) nmin = s->n_alloc; }
            sum.n_alloc += s->n_alloc; sum.n_free += s->n_free;
            sum.cyc_alloc += s->cyc_alloc; sum.cyc_free += s->cyc_free;
            if (s->max_alloc > sum.max_alloc) sum.max_alloc = s->max_alloc;
            if (s->max_free > sum.max_free) sum.max_free = s->max_free;
            for (int b = 0; b < NHIST; b++) { sum.hist_alloc[b] += s->hist_alloc[b]; sum.hist_free[b] += s->hist_free[b]; }
            for (int b = 0; b < NDEPTH; b++) sum.hist_depth[b] += s->hist_depth[b];
        }
        if (!sum.n_alloc && !sum.n_free) continue;
        fprintf(out, "{\"type\":\"phase\",\"phase\":%d,\"threads_active\":%d,\"n_alloc\":%llu,\"n_free\":%llu,"
                     "\"cyc_alloc\":%llu,\"cyc_free\":%llu,\"max_alloc\":%llu,\"max_free\":%llu,"
                     "\"n_alloc_thr_min\":%llu,\"n_alloc_thr_max\":%llu",
                k, active, (unsigned long long)sum.n_alloc, (unsigned long long)sum.n_free,
                (unsigned long long)sum.cyc_alloc, (unsigned long long)sum.cyc_free,
                (unsigned long long)sum.max_alloc, (unsigned long long)sum.max_free,
                (unsigned long long)(active ? nmin : 0), (unsigned long long)nmax);
        put_arr("hist_alloc_log2cyc", sum.hist_alloc, NHIST);
        put_arr("hist_free_log2cyc", sum.hist_free, NHIST);
        if (mode == 0) put_arr("hist_depth", sum.hist_depth, NDEPTH);
        fprintf(out, "}\n");
    }
    /* 呼び出し元：全スレッドで足し、dladdr で関数名にする */
    uintptr_t ca[256]; uint64_t cn[256]; int nc = 0;
    for (int j = 0; j < nt; j++) for (int i = 0; i < NCALLER && threads[j]->caller[i]; i++) {
        int f = 0;
        for (int q = 0; q < nc; q++) if (ca[q] == threads[j]->caller[i]) { cn[q] += threads[j]->caller_n[i]; f = 1; break; }
        if (!f && nc < 256) { ca[nc] = threads[j]->caller[i]; cn[nc++] = threads[j]->caller_n[i]; }
    }
    for (int q = 0; q < nc; q++) {
        Dl_info di; const char *sym = "?", *obj = "?"; uintptr_t off = 0;
        if (dladdr((void *)ca[q], &di)) {
            if (di.dli_sname) { sym = di.dli_sname; off = ca[q] - (uintptr_t)di.dli_saddr; }
            if (di.dli_fname) obj = di.dli_fname;
        }
        fprintf(out, "{\"type\":\"caller\",\"sym\":\"%s\",\"off\":%lu,\"obj\":\"%s\",\"n\":%llu}\n",
                sym, (unsigned long)off, obj, (unsigned long long)cn[q]);
    }
    fflush(out);
}
