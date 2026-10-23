# Agent Context Controller

[English](README.md) | 繁體中文

本機工具，讓 Codex CLI、Claude Code 在每次 `UserPromptSubmit` 都能讀到你自己維護的
Rapport（目前想怎麼合作）與 Goals（長期目標／專案目標／這個對話的 Objective）。
純 Python 標準函式庫，沒有任何 pip 依賴。

## 安裝

把 `bin/acc` 建個 symlink 到 PATH 上（例如 `~/.local/bin`）：

```bash
ln -s "$(pwd)/bin/acc" ~/.local/bin/acc   # 在 repo 根目錄執行
```

資料預設放在 Windows 使用者目錄下的 `.agent-context`（WSL 裡是
`/mnt/c/Users/<你的名字>/.agent-context`，Windows 瀏覽器與 WSL 裡的 Agent 都讀得到）；
不是 WSL 時放在 `~/.agent-context`。第一次判斷的結果記在 `~/.config/acc/root`，
要換位置可以改這個檔，或設定環境變數 `ACC_HOME`（優先）。第一次讀取時會自動建立
目錄與預設檔案。

## 接上 Agent

每輪的 hook 只注入「狀態」；Agent 該怎麼依使用者的話更新狀態，寫在
`<資料目錄>/templates/rules.md`，要另外裝進 Agent 的全域指令檔。

### Claude Code

1. `~/.claude/settings.json`：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "acc hook claude", "timeout": 10 } ] }
    ]
  },
  "permissions": { "allow": ["Bash(acc objective:*)", "Bash(acc super:*)"] }
}
```

2. `acc rules install claude`：在 `~/.claude/CLAUDE.md` 加兩行匯入 `rules.md` 與
   `goals/ultimate.md`（都是即時匯入，改完不必重裝，新對話就會讀到）。

### Codex CLI

1. `~/.codex/hooks.json`：

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "acc hook codex", "timeout": 10}]}]}}
```

2. `~/.codex/config.toml`：讓沙盒可以寫資料目錄，否則 Agent 執行 `acc objective …` 會失敗：

```toml
[sandbox_workspace_write]
writable_roots = ["/mnt/c/Users/<你的名字>/.agent-context"]
```

3. `acc rules install codex`：把 `rules.md` 全文和目前的 Ultimate（`goals/ultimate.md`）一起
   複製進 `~/.codex/AGENTS.md`（Codex 沒有匯入語法，改其中任何一個都要重跑）。
4. 打開 Codex，在介面裡審核並信任這個 hook，它才會執行。

規則、hook、Ultimate Goal／Definition 都是對話開始時載入；已經開著的對話要重開。hook 出任何
錯都不會擋住訊息：錯誤寫進 `<資料目錄>/logs/hook.log`，不輸出、exit 0。

Ultimate 之後改變不需要重開對話：每輪的 Goals 會比對這個對話一開始載入的版本，有變化才附上
一段「已更新」的完整內容（並記住已經送過），沒變化就完全不提。使用者在網頁存 Ultimate 時，
如果 Codex 之前裝過，會自動重跑一次 `install`，讓「之後開的新對話」也拿到新版（已經開著的
Codex 對話手上的複本不會變，這是那段「已更新」注入要補的洞）。

## 網頁面板

```bash
acc serve            # http://localhost:8765，--port／--host 可覆寫
```

- **對話**（最上面）：每個對話一張卡片，有顏色條、暱稱、短 id、最後活動時間。展開後：
  - 這個對話的 Rapport（或跟隨預設）｜所屬專案
  - 進行中的 Objective 與 Success Criteria、修改紀錄，按鈕：編輯／成功／擱置／失敗
  - 擱置清單：拖曳或上下排序、「切上來」與目前的對調、移除
  - 已結案時間線：每段進行／擱置的時間、送出當下的 Rapport 變化、重新開啟
  - 封存；超過 `idle_hours`（預設 6）沒訊息的對話收進「閒置」
- **Projects**：每個專案的 Super Objective、變更紀錄、Agent 提出的建議（採用／不採用）
- **Ultimate**（最下面）：Ultimate Goal／Definition 與變更紀錄
- **右下角懸浮窗**：最上面是「工作中／休息」開關，顯示起始時間；縮小成藥丸時休息中會顯示「休息中」。這只是使用者自己的紀錄，不影響任何注入內容。下面才是預設 Rapport；「注入 Rapport」開關（全域）；「編輯 Preset」開彈窗管理 Preset 與各 Level 的 Prompt
- **Rapport 開關**：全域（懸浮窗開關）之外，每個對話卡片內有「跟隨全域／開／關」三段選擇；關閉時那輪的 context 不含 `<Rapport>`，時間軸與工作紀錄會標「未送出」
- **複製工作紀錄**：主按鈕「複製工作紀錄」一律複製全部；旁邊的「▾」開範圍選單：全部／最新一次工作階段／指定範圍…（兩個下拉選單挑工作階段 + 複製）。「檢視」面板顯示的是同一個選到的範圍，標題會註明目前範圍。

## CLI

```bash
acc root                                    # 印出資料根目錄
acc context --session ID [--cwd PATH]       # 預覽 context（不會動到 session 檔）
acc objective show --session ID
acc objective set  --session ID "<目標>" --criteria "<完成標準>"
acc objective done --session ID --note "<依據>"
acc objective fail --session ID --note "<原因>"
acc objective park --session ID --reason "<使用者怎麼說的>"
acc super suggest  --session ID "<新的 Super Objective>" --reason "<理由>"
acc super show     --session ID
acc objective revise --session ID ["<新目標>"] [--criteria "<新標準>"] --reason "<理由>"
acc objective log    --session ID           # 印出這個 session 的工作紀錄（Markdown）
acc objective log    --session ID --latest-work          # 只印最新一次工作階段
acc objective log    --session ID --work-from N --work-to M  # 只印指定範圍的工作階段
acc objective list   --session ID           # 印出進行中／擱置清單／已結案（最近 10 筆），附 P<n>/H<n> id 與 oid
acc objective amend  --session ID --id <P1|H2|oid> --to achieved|failed|parked|active --reason "<理由>" [--note "<新備註>"]
acc work status                             # 目前工作中／休息中，第幾次工作階段、起始時間
acc work start                              # 切回工作中（開新的一次工作階段）
acc work stop                               # 切到休息中
acc rules show                              # 印出 templates/rules.md
acc rules install claude|codex [--target P] # 把規則寫進 ~/.claude/CLAUDE.md 或 ~/.codex/AGENTS.md
acc serve [--port N] [--host 127.0.0.1]
```

每輪注入只有狀態；Agent 該怎麼判斷、用哪個指令，寫在 `templates/rules.md`，
由 `acc rules install` 放進 Agent 的全域指令檔（Claude 用匯入，Codex 複製全文，
所以改完 rules.md 要對 Codex 重跑一次 install）。

`objective set` 在該 session 已有進行中的 Objective 時會報錯——要先 `done` 或
`fail` 結案，或用 `park` 擱置，才能換下一個。GUI 上的編輯是使用者操作，可以
直接改任何欄位。

`acc objective park` 把目前進行中的 Objective 整包搬進這個 session 的「擱置
清單」（不會結案，也不影響 history），改回沒有進行中的 Objective，好讓 Agent
可以 `set` 一個新的。擱置的 Objective**不會**出現在注入的 context 裡。之後
`done`／`fail` 把目前的 Objective 結案時，如果擱置清單不是空的，會自動把清單
最前面那個接回來變成新的進行中 Objective（CLI 會多印一行「已自動接回：
<目標>」）；網頁另外可以「切上來」（跟目前進行中的互換位置）、拖曳或用
上／下按鈕排序、「移除」（會進 history，狀態是 dropped）。

`acc objective amend` 用來更正先前一次 `done`／`fail`／`park` 判斷錯了的情況（成功
判成失敗、其實只是擱置卻結案了……）：`acc objective list` 先找到那一筆的短 id
（擱置清單 P1、P2…；最近 10 筆已結案 H1、H2…；有 oid 時 oid 也能直接用），再用
`amend --id <id> --to <目標狀態> --reason "<為什麼先前判斷不對>"`。CLI（Agent）
必須帶 `--reason`；網頁（使用者操作）不強制。`--note` 沒給就保留原本的備註。
每次更正都記一筆 `objective.amended` 事件，工作紀錄與網頁時間軸都會顯示「已更正」。

`acc super suggest` 只會提出一則待決定的建議（存在
`goals/projects/<key>.suggestions.json`），**不會**改動 Super Objective 本身——
Agent 沒有直接修改 Super Objective 的指令。網頁的 Projects 區塊會顯示待決定的
建議，使用者按「採用」才會真的套用（並記一筆 `super.changed`），按「不採用」
就標記為 rejected。`acc super show` 印出目前的 Super Objective 與該 session
所屬專案目前待決定的建議。

## 事件紀錄

`<ACC_HOME>/events.jsonl` 是一份只增不減的紀錄：每次會改變狀態的操作（Rapport
調整、Objective 設定/結案/編輯、Super Objective 變更/建議、Session
建立/封存/改名……）都會在這裡多一行 JSON，格式是
`{"at": "<ISO 時間>", "type": "<事件類型>", "actor": "user"|"agent"|"hook", ...}`。
其中 `turn` 事件是每次 hook 呼叫都會記一筆（不管這輪跟上一輪有沒有變化），帶著
當下的 Rapport/Objective/Super Objective 狀態，但絕對不含 prompt 全文（只有
`prompt_chars` 這個長度）。

`<ACC_HOME>/.snapshots.json` 是外部編輯偵測用的雜湊快照（`goals/ultimate.md`、
`goals/projects/*.md`、`templates/*.md`、`rapport/presets/*/*.md`）；每次 hook
呼叫都會比對一次，發現跟上次不一樣（而且不是 acc 自己寫的）就記一筆
`external_edit` 事件。目前這份紀錄只寫入，還沒有專門的統計介面——網頁的
Ultimate / Super Objective 變更紀錄是直接從這份 log 讀出來顯示的。

## 開發 / 測試

```bash
python3 -m unittest discover -s tests
```
