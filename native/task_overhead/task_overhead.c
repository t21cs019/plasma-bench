/*
 * task_overhead.c — タイル QR と同じ依存関係のタスクを流し、タスク1個あたりの時間 τ を測る。
 *
 * PLASMA の dgeqrf と同じ形にしている。
 *   - #pragma omp parallel + master の中で、1本のスレッドがタスクを投げる
 *   - geqrt / unmqr / tsqrt / tsmqr の順に、同じ depend 句を付ける
 * タスクの中身は空か、--work-ns で指定した時間だけ待つ。行列は持たない。
 *
 * 計時の前に一度 parallel を実行して、スレッドの生成を計時から外す。
 *
 * 出力（1反復 1行）:
 *   runtime,threads,tiles,ntasks,work_ns,iter,total_sec,submit_sec,tau_us
 *     total_sec   最初のタスクを投げる直前から、全タスクの終了まで
 *     submit_sec  投げ終わるまで。ランタイムがタスクを溜めすぎると投げる側も実行に回るので、
 *                 純粋な生成時間とは限らない
 *     tau_us      (total_sec - ntasks * work_ns / threads) / ntasks
 */
#define _GNU_SOURCE
#include <getopt.h>
#include <omp.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef RUNTIME_NAME
#define RUNTIME_NAME "unknown"
#endif

static long work_ns = 0;

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static void task_body(void)
{
    if (work_ns <= 0)
        return;
    double end = now() + work_ns * 1e-9;
    while (now() < end)
        ;
}

/* タイル QR の DAG を投げる。投げたタスクの数を返す。 */
static long submit_tasks(int n, char (*A)[n], char (*T)[n])
{
    /* A, T は depend 句の中でしか使わないので、gcc が未使用と誤って警告する */
    (void)A;
    (void)T;
    long count = 0;
    for (int k = 0; k < n; k++) {
        #pragma omp task depend(inout:A[k][k]) depend(out:T[k][k])
        task_body();
        count++;

        for (int j = k + 1; j < n; j++) {
            #pragma omp task depend(in:A[k][k]) depend(in:T[k][k]) depend(inout:A[k][j])
            task_body();
            count++;
        }
        for (int m = k + 1; m < n; m++) {
            #pragma omp task depend(inout:A[k][k]) depend(inout:A[m][k]) depend(out:T[m][k])
            task_body();
            count++;

            for (int j = k + 1; j < n; j++) {
                #pragma omp task depend(inout:A[k][j]) depend(inout:A[m][j]) \
                                 depend(in:A[m][k]) depend(in:T[m][k])
                task_body();
                count++;
            }
        }
    }
    return count;
}

/* スレッドを作り、各スレッドが動ける CPU の集合が何通りあるかを出す。 */
static void report_affinity(void)
{
    int max = omp_get_max_threads();
    int nthreads = 0;
    cpu_set_t *sets = calloc(max, sizeof(cpu_set_t));
    if (sets == NULL) {
        fprintf(stderr, "out of memory\n");
        exit(1);
    }
    #pragma omp parallel
    {
        #pragma omp master
        nthreads = omp_get_num_threads();
        sched_getaffinity(0, sizeof(cpu_set_t), &sets[omp_get_thread_num()]);
    }
    int distinct = 0;
    for (int i = 0; i < nthreads; i++) {
        int first = 1;
        for (int j = 0; j < i; j++)
            if (CPU_EQUAL(&sets[i], &sets[j]))
                first = 0;
        distinct += first;
    }
    printf("# affinity threads=%d distinct_sets=%d\n", nthreads, distinct);
    free(sets);
}

int main(int argc, char **argv)
{
    int n = 0, iters = 1;
    static struct option opts[] = {
        {"tiles",   required_argument, 0, 't'},
        {"work-ns", required_argument, 0, 'w'},
        {"iters",   required_argument, 0, 'i'},
        {0, 0, 0, 0},
    };
    int c;
    while ((c = getopt_long(argc, argv, "", opts, NULL)) != -1) {
        switch (c) {
        case 't': n = atoi(optarg); break;
        case 'w': work_ns = atol(optarg); break;
        case 'i': iters = atoi(optarg); break;
        default:  return 2;
        }
    }
    if (n <= 0 || iters <= 0 || work_ns < 0) {
        fprintf(stderr, "usage: %s --tiles N [--work-ns NS] [--iters R]\n", argv[0]);
        return 2;
    }

    char (*A)[n] = calloc(n, n);
    char (*T)[n] = calloc(n, n);
    if (A == NULL || T == NULL) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }

    report_affinity();
    int threads = omp_get_max_threads();

    for (int iter = 1; iter <= iters; iter++) {
        long ntasks = 0;
        double submitted = 0.0;
        double start = now();
        #pragma omp parallel
        #pragma omp master
        {
            ntasks = submit_tasks(n, A, T);
            submitted = now();
        }
        double total = now() - start;
        double tau = (total - ntasks * (work_ns * 1e-9) / threads) / ntasks;
        printf("%s,%d,%d,%ld,%ld,%d,%.6f,%.6f,%.4f\n", RUNTIME_NAME, threads, n, ntasks,
               work_ns, iter, total, submitted - start, tau * 1e6);
        fflush(stdout);
    }

    free(A);
    free(T);
    return 0;
}
