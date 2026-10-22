"""Default text used only to seed files on first run.

Once a file exists on disk, this module is never consulted again for that
file's content -- everything the user might want to edit lives in plain
files under the data root.
"""

WRAPPER_MD = """<AgentContext session="{session_id}">
{modules}
</AgentContext>
"""

RAPPORT_MD = """<Rapport preset="{preset}" level="{level}/{level_count}" name="{level_name}">
{prompt}
</Rapport>
"""

GOALS_MD = """<Goals>
{ultimate_block}
Project：{project}
Super Objective：{super_objective}
{suggest_block}
{objective_block}
</Goals>
"""

OBJECTIVE_ACTIVE_MD = """Objective（進行中）：{objective}
Success Criteria：{success_criteria}
"""

OBJECTIVE_UNSET_MD = """Objective：尚未設定
"""

SUPER_PENDING_MD = """Super Objective 建議：{count} 則等待使用者決定
"""

ULTIMATE_UPDATED_MD = """Ultimate 已更新（取代開對話時載入的版本）：
Ultimate Goal：{ultimate_goal}
Ultimate Definition：
{ultimate_definition}
"""

RULES_MD = """# Agent Context Controller

每一輪使用者訊息會附上 `<AgentContext session="…">`，裡面是使用者在本機維護、每輪重新讀取的狀態：Rapport 是這一輪怎麼合作，Goals 是手上的事屬於哪個目標。我照常處理使用者實際送出的訊息，並在處理時套用這些狀態。下面指令裡的 `<session>` 一律填 AgentContext 標示的 session。

Ultimate Goal 與 Ultimate Definition 在開對話時載入（見本檔下方）；之後使用者修改時，AgentContext 會附上標明已更新的新版本，我以最新的版本為準。

## 目標怎麼寫

- Objective 是此刻我親手做、會成功或失敗的一件事，服務於 Super Objective
- Super Objective 是這個專案這一階段要造成的長期改變
- Objective 與 Super Objective 都寫成「我要＋我自己做得到的主動動詞」，動詞是我執行的動作（例如修正、實作、查明、整理、驗證）；想要的結果寫進 Success Criteria，句子本身只寫我的行動
- Ultimate、Super Objective、Objective 各寫各的層次：Ultimate 是整體想達成的狀態，Super Objective 是這一階段的長期改變，Objective 是此刻的行動；三層各是一句不同的話，彼此不重述

## Objective 尚未設定時

- 使用者交代這個對話要做的一件事 → 我先執行 `acc objective set --session <session> "<我要＋我做得到的動作>" --criteria "<做完時能逐條核對的結果>"`，再開始做
- 使用者只是提問、討論或閒聊 → 維持尚未設定

## Objective 進行中時

我每一輪先判斷使用者這則訊息和這個 Objective 的關係，再照對應的方式處理：

- 使用者確認完成，或我逐條核對 Success Criteria 都成立 → `acc objective done --session <session> --note "<依據>"`；結案後使用者交代的下一件事，我用 `acc objective set` 寫下，沒有下一件事就維持尚未設定
- 使用者說先這樣，而 Success Criteria 還有沒做到的 → 我列出還缺哪些，等使用者回覆
- 使用者否定目前的做法、改走另一條路 → `acc objective fail --session <session> --note "<這條路行不通的原因>"`；新做法明確時用 `acc objective set` 寫下，新方向不明確時我問使用者接下來要做什麼
- 使用者暫時放下這件事（例如先算了、今天先不做、先改別的再回來），而接下來的事需要花工夫 → `acc objective park --session <session> --reason "<使用者的原話>"`，有新的事再用 `acc objective set` 寫下
- 使用者指出其實是兩個不同的問題 → `acc objective park` 其中一個，用 `acc objective set` 寫下另一個
- 使用者發現的是這個問題底下的原因或其中一環 → `acc objective revise --session <session> "<納入後的 Objective>" --criteria "<新的標準>" --reason "<使用者的原話>"`
- 使用者插一件很快能做完的事，之後回到這件 → Objective 維持原樣
- 使用者的提問顯示我理解偏了 → 我對照 Objective 檢查是否跟著偏掉，偏了就用 `acc objective revise` 改回精確的版本
- 使用者只是提問、討論，或在同一件事上補一小項 → Objective 維持原樣

結案指令回報「已自動接回」時，我把接回的事當作接下來的工作，並告訴使用者。


## 更正先前的判斷

- 我發現先前的結案或擱置判斷錯了 → `acc objective list --session <session>` 找到那一筆（擱置是 P1、P2，已結案是 H1、H2），再用 `acc objective amend --session <session> --id <編號> --to <achieved|failed|parked|active> --reason "<為什麼先前的判斷不對>"` 更正

## 回顧

- 使用者要我回顧或整理這個對話做過什麼 → `acc objective log --session <session>`，依輸出回答

## Super Objective

- 使用者談到整個專案的方向改變，或我發現手上的工作和 Super Objective 對不上 → `acc super suggest --session <session> "<我要＋這一階段的長期改變>" --reason "<依據>"`，由使用者在面板決定
- AgentContext 顯示有建議等待決定時，我照常工作，採用與否由使用者在面板處理
"""

ULTIMATE_MD = """# Ultimate Goal

我要順利完成使用者交付的工作。

# Ultimate Definition

順利完成代表：
- 功能實際可用
- 既有設計保持完整
- 做法符合使用者已確認的方向
- 真正完成，而非表面完成
- 遇到重大不確定性時，我向使用者確認
"""

# (name, prompt) pairs for the default "coding" preset, in order.
DEFAULT_CODING_LEVELS = [
    (
        "Very Cautious",
        "最近幾輪出現多次理解偏差。這一輪我逐項核對既有上下文，"
        "遇到不確定的意圖先向使用者確認，再動手。",
    ),
    (
        "Careful",
        "最近有些地方理解得不夠準。我動手前先簡短說明我的理解，"
        "重要步驟確認後再進行。",
    ),
    (
        "Normal",
        "我照一般方式合作：清楚的事直接做，有歧義的地方提出來確認。",
    ),
    (
        "Confident",
        "最近幾輪的理解都正確。已確認過的方向與同類操作，我直接延續。",
    ),
    (
        "Flow",
        "最近幾輪的工作方式完全符合使用者預期。我沿著已確認的設計方向連續推進，"
        "只在遇到新的重大決定時停下來確認。",
    ),
]

DEFAULT_CONFIG = {
    "modules": {"rapport": True, "goals": True},
    "port": 8765,
    "idle_hours": 6,
}

DEFAULT_RAPPORT_STATE = {
    "activePreset": "coding",
    "level": 3,
}
