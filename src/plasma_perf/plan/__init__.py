"""plan — 計測進捗の管理と、未計測分の自動実行。

`plan/PROGRESS.md` に ssrfb / tileqr / tuning の3つの表を持ち、
- `plan scan` : results/ を走査して status/coverage を自動更新（計画列・note は保持）
- `plan add`  : 直積展開で行を簡単に追加
- `plan run`  : 未計測（missing/partial）を上から実行。partial は未充足 nb から再開
"""
