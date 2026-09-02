"""
run_tuning.py — チューニング手法の実測比較 CLI。

「正解を知らない状態から、各手法が何秒で何 GFlops に到達するか」を測る。
既存の benchmark.py（nb×ib 全スイープ）とは別物（指示書 0節）。

事前準備（tuning/ 用の Python 環境。計測コア src/ は標準ライブラリのみで動くので
benchmark.py だけを使うなら不要）:
    bash scripts/setup_python.sh --extra tuning

使い方:
    uv run python tuning/run_tuning.py \\
        --size 4096 \\
        --methods agullo,optuna_full,model \\
        --repeat 10 \\
        --threads 4 \\
        --cooldown 30 \\
        --output results/tuning/

    # 実行回数・推定時間だけ見る
    uv run python tuning/run_tuning.py --dry-run --size 4096 --methods agullo,optuna_full,model
"""
import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import time
import datetime

# tmux + tee 経由（scripts/run_tuning_campaign.sh）で実行するとパイプ先が非tty
# になり、CPython の既定動作でこのプロセスの stdout がブロックバッファ
# （デフォルト8KB程度）に切り替わる。結果、進捗の print()（"--- run" / "-> nb="
# 等）がバッファに溜まったまま何時間も flush されず、ログ tail や監視が長時間
# 無音に見える（2026-09-01 実機で確認: 約7時間・agullo 2回分の進捗が一切
# ログに出ていなかった）。CSV 書き込みは1行ごとに明示 flush されるため無事だが、
# 進捗の可観測性のために stdout を明示的に行バッファへ切り替える。
sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from tuning.methods import agullo, optuna_full, model  # noqa: E402
except ImportError as e:
    print(f"[ERROR] 依存パッケージが不足しています: {e}")
    print("        `bash scripts/setup_python.sh` を実行して環境を構築し、"
          "`uv run python tuning/run_tuning.py ...` で起動してください。")
    sys.exit(1)
from tuning import beta_fit  # noqa: E402

CSV_COLUMNS = [
    "host", "size", "threads", "physical_cores", "smt_active", "method", "run_id", "seed",
    "nb", "ib", "time_total_sec", "time_prep_sec", "n_qr_exec", "n_kernel_exec",
    "beta", "band_lo", "band_hi", "gflops_observed", "notes",
]

METHOD_MODULES = {
    "agullo": agullo,
    "optuna_full": optuna_full,
    "model": model,
}

# 指示書 5節の粗い目安（size=4096, threads=4, i3-10100 想定）。
# --dry-run の推定表示にのみ使う。実測とはズレうる。
# kernel_reps/beta_reps は NoFlush 内部の N_IT に渡す設計（kernel_bench.py 冒頭
# 参照）なので、1点=1プロセス起動。ROUGH_SEC_PER_KERNEL_LAUNCH は
# kernel_reps=DEFAULT_KERNEL_REPS(=50), nb=256 相当の1起動あたりの粗い目安。
ROUGH_SEC_PER_QR_CALL_AT_4096 = 2.0
ROUGH_SEC_PER_KERNEL_LAUNCH_AT_NB256_REPS50 = 0.6
ROUGH_KERNEL_LAUNCH_NB_REF = 256
ROUGH_KERNEL_LAUNCH_REPS_REF = 50
AGULLO_ROUGH_TOTAL_SEC_AT_4096_REPS50 = 4878.0


def get_physical_cores() -> int:
    return model.get_physical_cores()


def get_smt_active():
    """lscpu -p=CPU,CORE から SMT 有効/無効を判定する。

    Returns
    -------
    (active: bool, detail: str)
    """
    try:
        out = subprocess.run(
            ["lscpu", "-p=CPU,CORE"], capture_output=True, text=True, check=True
        ).stdout
    except Exception as e:
        return None, f"lscpu実行失敗: {e}"

    core_to_cpus = {}
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        cpu, core = line.strip().split(",")
        core_to_cpus.setdefault(core, []).append(cpu)

    active = any(len(cpus) > 1 for cpus in core_to_cpus.values())
    detail = f"cores={len(core_to_cpus)}, cpus={sum(len(v) for v in core_to_cpus.values())}"
    return active, detail


def save_provenance(output_dir: str, base_name: str, threads: int, kernel_threads: int = 1):
    """実行開始時のプロベナンスを保存する（指示書 1.5）。"""
    prov_dir = os.path.join(output_dir, f"{base_name}.provenance")
    os.makedirs(prov_dir, exist_ok=True)

    try:
        lscpu_out = subprocess.run(
            ["lscpu"], capture_output=True, text=True, check=True
        ).stdout
    except Exception as e:
        lscpu_out = f"[ERROR] lscpu failed: {e}"
    with open(os.path.join(prov_dir, "lscpu.txt"), "w") as f:
        f.write(lscpu_out)

    smt_active, smt_detail = get_smt_active()
    with open(os.path.join(prov_dir, "smt_active.txt"), "w") as f:
        f.write(f"smt_active={smt_active}\n{smt_detail}\n")

    try:
        git_rev = subprocess.run(
            ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception as e:
        git_rev = f"unknown ({e})"

    info = {
        "host": socket.gethostname(),
        "nproc": os.cpu_count(),
        "physical_cores": get_physical_cores(),
        "OMP_NUM_THREADS_requested": threads,
        "kernel_threads": kernel_threads,
        "smt_active": smt_active,
        "git_rev_plasma_bench": git_rev,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    with open(os.path.join(prov_dir, "provenance.json"), "w") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

    return prov_dir, smt_active


def save_row(output_csv: str, row: dict, write_header: bool):
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    with open(output_csv, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def build_output_csv_path(output_dir: str, host: str, size: int, threads: int) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = f"tuning_{host}_size{size}_th{threads}_{ts}"
    return os.path.join(output_dir, f"{base_name}.csv"), base_name


def estimate_dry_run(methods, size, threads, repeat, agullo_kwargs, optuna_kwargs, model_kwargs):
    scale = (size / 4096.0) ** 3  # QR/カーネル実行1回あたりの粗いサイズスケーリング
    lines = []
    for m in methods:
        if m == "agullo":
            nb_min = agullo_kwargs.get("nb_min", agullo.DEFAULT_NB_MIN)
            nb_max = agullo_kwargs.get("nb_max", agullo.DEFAULT_NB_MAX)
            nb_step = agullo_kwargs.get("nb_step", agullo.DEFAULT_NB_STEP)
            ib_step = agullo_kwargs.get("ib_step", agullo.DEFAULT_IB_STEP)
            kernel_reps = agullo_kwargs.get("kernel_reps", agullo.DEFAULT_KERNEL_REPS)
            n_nb = len(range(nb_min, nb_max + 1, nb_step))
            n_kernel_exec = agullo.estimate_n_launches(nb_min, nb_max, nb_step, ib_step)
            n_qr_exec = 8  # ヒューリスティック2で最大8組
            # 1起動あたりの粗い目安は nb^3・kernel_reps(=N_IT) に比例するとして概算する
            # （kernel_reps は NoFlush 内部の N_IT に渡る。kernel_bench.py 冒頭参照）。
            avg_nb = (nb_min + nb_max) / 2.0
            per_launch_sec = (
                ROUGH_SEC_PER_KERNEL_LAUNCH_AT_NB256_REPS50
                * (kernel_reps / ROUGH_KERNEL_LAUNCH_REPS_REF)
                * (avg_nb / ROUGH_KERNEL_LAUNCH_NB_REF) ** 3
            )
            est_sec = (
                n_kernel_exec * per_launch_sec * scale
                + n_qr_exec * ROUGH_SEC_PER_QR_CALL_AT_4096 * scale
            )
            lines.append(
                f"[agullo]      n_kernel_exec≈{n_kernel_exec} (nb点数{n_nb}×ib点数, ib_step{ib_step}, "
                f"kernel_reps(N_IT){kernel_reps}), n_qr_exec<=8, "
                f"推定時間≈{est_sec:.0f}秒(粗い目安・要キャリブレーション)/回 × repeat={repeat}"
            )
        elif m == "optuna_full":
            n_trials = optuna_kwargs.get("n_trials", optuna_full.N_TRIALS)
            qr_reps = optuna_kwargs.get("qr_reps", optuna_full.QR_REPS)
            n_qr_exec = n_trials * qr_reps
            est_sec = n_qr_exec * ROUGH_SEC_PER_QR_CALL_AT_4096 * scale
            lines.append(
                f"[optuna_full] n_qr_exec={n_qr_exec} (n_trials{n_trials}×qr_reps{qr_reps}), "
                f"推定時間≈{est_sec:.0f}秒(粗い目安)/回 × repeat={repeat}"
            )
        elif m == "model":
            beta_reps = model_kwargs.get("beta_reps", 1)
            n_trials = model_kwargs.get("n_trials", model.N_TRIALS)
            top_k = model_kwargs.get("top_k", model.TOP_K)
            verify_reps = model_kwargs.get("verify_reps", model.VERIFY_REPS)
            n_kernel_exec = beta_fit.estimate_n_launches()
            n_qr_exec = n_trials + top_k * verify_reps
            per_launch_sec = (
                ROUGH_SEC_PER_KERNEL_LAUNCH_AT_NB256_REPS50
                * (beta_reps / ROUGH_KERNEL_LAUNCH_REPS_REF)
            )  # β計測点(nb 64-512)はほぼ ROUGH_KERNEL_LAUNCH_NB_REF 相当なので nb 項は省略
            est_sec = (
                n_kernel_exec * per_launch_sec * scale
                + n_qr_exec * ROUGH_SEC_PER_QR_CALL_AT_4096 * scale
            )
            lines.append(
                f"[model]       n_kernel_exec={n_kernel_exec} (β計測, beta_reps(N_IT){beta_reps}), "
                f"n_qr_exec={n_qr_exec} (n_trials{n_trials}+top_k{top_k}×verify_reps{verify_reps}), "
                f"推定時間≈{est_sec:.0f}秒(粗い目安・要キャリブレーション)/回 × repeat={repeat}"
            )
    return lines


def parse_args():
    p = argparse.ArgumentParser(description="チューニング手法の実測比較")
    p.add_argument("--size", type=int, default=4096)
    p.add_argument("--methods", type=str, default="agullo,optuna_full,model")
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--threads", type=int, default=os.cpu_count(),
                    help="フルQR(tileqr)測定用のスレッド数。dtsmqr/SSRFBカーネル計測には"
                         "使わない（既存の ssrfb ベンチ規約に合わせて常に1。--kernel-threads 参照）")
    p.add_argument("--kernel-threads", type=int, default=agullo.DEFAULT_KERNEL_THREADS,
                    help="dtsmqr/SSRFB カーネル計測（agullo のグリッド計測・model のβ計測）用の"
                         "スレッド数。既定1（src/plasma_perf/benchmarks/cli.py の "
                         "_DEFAULT_THREADS['ssrfb'] に合わせる）。--threads とは独立")
    p.add_argument("--cooldown", type=int, default=30)
    p.add_argument("--output", type=str, default=os.path.join(REPO_ROOT, "results", "tuning"))
    p.add_argument("--dry-run", action="store_true")

    # agullo
    p.add_argument("--nb-min", type=int, default=agullo.DEFAULT_NB_MIN)
    p.add_argument("--nb-max", type=int, default=agullo.DEFAULT_NB_MAX)
    p.add_argument("--nb-step", type=int, default=agullo.DEFAULT_NB_STEP)
    p.add_argument("--ib-step", type=int, default=agullo.DEFAULT_IB_STEP)
    p.add_argument("--kernel-reps", type=int, default=agullo.DEFAULT_KERNEL_REPS)

    # optuna_full
    p.add_argument("--n-trials-optuna", type=int, default=optuna_full.N_TRIALS)
    p.add_argument("--qr-reps-optuna", type=int, default=optuna_full.QR_REPS)
    p.add_argument("--nb-min-optuna", type=int, default=optuna_full.NB_MIN,
                    help="optuna_full の nb 探索下限。既定は卒論通り4だが、"
                         "小さすぎる nb は PLASMA のタスク数が (size/nb)^3 で"
                         "爆発しOOMを招くため、機種によっては引き上げが必要"
                         "（卒論再現から逸脱するため notes 列に記録される）")
    p.add_argument("--nb-max-optuna", type=int, default=optuna_full.NB_MAX)

    # model
    p.add_argument("--beta", type=float, default=None, help="外部から与えるβ(指定時は計測をスキップ)")
    p.add_argument("--beta-reps", type=int, default=1)
    p.add_argument("--n-trials-model", type=int, default=model.N_TRIALS)
    p.add_argument("--top-k-model", type=int, default=model.TOP_K)
    p.add_argument("--verify-reps-model", type=int, default=model.VERIFY_REPS)

    p.add_argument("--noflush-exe", type=str, default=None)

    return p.parse_args()


def main():
    args = parse_args()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    for m in methods:
        if m not in METHOD_MODULES:
            print(f"[ERROR] 未知の手法です: {m} (選択肢: {list(METHOD_MODULES)})")
            sys.exit(1)

    agullo_kwargs = dict(
        nb_min=args.nb_min, nb_max=args.nb_max, nb_step=args.nb_step, ib_step=args.ib_step,
        kernel_reps=args.kernel_reps, kernel_threads=args.kernel_threads, exe=args.noflush_exe,
    )
    optuna_kwargs = dict(
        n_trials=args.n_trials_optuna, qr_reps=args.qr_reps_optuna,
        nb_min=args.nb_min_optuna, nb_max=args.nb_max_optuna,
    )
    model_kwargs = dict(
        beta=args.beta, beta_reps=args.beta_reps, n_trials=args.n_trials_model,
        top_k=args.top_k_model, verify_reps=args.verify_reps_model,
        kernel_threads=args.kernel_threads,
        noflush_exe=args.noflush_exe,
    )

    if args.dry_run:
        print("=" * 60)
        print(" dry-run: 実行回数・推定時間の見積もり")
        print(f"  size={args.size}, threads={args.threads}, repeat={args.repeat}")
        print(f"  methods={methods}")
        print("=" * 60)
        for line in estimate_dry_run(
            methods, args.size, args.threads, args.repeat,
            agullo_kwargs, optuna_kwargs, model_kwargs,
        ):
            print(line)
        return

    host = socket.gethostname()
    output_csv, base_name = build_output_csv_path(args.output, host, args.size, args.threads)
    physical_cores = get_physical_cores()

    prov_dir, smt_active = save_provenance(args.output, base_name, args.threads, args.kernel_threads)
    print(f"[info] プロベナンス保存先: {prov_dir}")
    print(f"[info] smt_active={smt_active}")

    beta_raw_csv = os.path.join(
        args.output, f"beta_{host}_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    model_kwargs["beta_raw_csv"] = beta_raw_csv

    write_header = not os.path.exists(output_csv)

    print("=" * 60)
    print(" チューニング手法の実測比較")
    print(f"  host={host}, size={args.size}, threads={args.threads} (QR用), "
          f"kernel_threads={args.kernel_threads} (dtsmqr/SSRFB用), "
          f"physical_cores={physical_cores}, smt_active={smt_active}")
    print(f"  methods={methods}, repeat={args.repeat}, cooldown={args.cooldown}s")
    print(f"  output={output_csv}")
    print("=" * 60)

    for run_id in range(args.repeat):
        seed = run_id
        for m in methods:
            print(f"\n--- run {run_id + 1}/{args.repeat}: method={m}, seed={seed} ---")
            if args.cooldown > 0:
                print(f"[cooldown] {args.cooldown}秒待機します...")
                time.sleep(args.cooldown)

            module = METHOD_MODULES[m]
            kwargs = {"agullo": agullo_kwargs, "optuna_full": optuna_kwargs, "model": model_kwargs}[m]

            t_wall0 = time.time()
            try:
                result = module.run(args.size, args.threads, seed, ctx=None, **kwargs)
            except Exception as e:
                print(f"[ERROR] {m} run_id={run_id} 失敗: {e}")
                result = {
                    "nb": None, "ib": None, "time_total_sec": time.time() - t_wall0,
                    "time_prep_sec": None, "n_qr_exec": 0, "n_kernel_exec": 0,
                    "beta": None, "band_lo": None, "band_hi": None,
                    "gflops_observed": None, "notes": f"EXCEPTION: {e}",
                }

            row = {
                "host": host, "size": args.size, "threads": args.threads,
                "physical_cores": physical_cores, "smt_active": smt_active,
                "method": m, "run_id": run_id, "seed": seed,
                **result,
            }
            save_row(output_csv, row, write_header)
            write_header = False

            print(
                f"  -> nb={result.get('nb')}, ib={result.get('ib')}, "
                f"time_total_sec={result.get('time_total_sec')}, "
                f"gflops_observed={result.get('gflops_observed')}"
            )

    print(f"\n[完了] 結果を保存しました: {output_csv}")


if __name__ == "__main__":
    main()
