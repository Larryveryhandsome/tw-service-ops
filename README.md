# tw-service-ops｜店家營運中樞（商家通用版）

[![tests](https://github.com/Larryveryhandsome/tw-service-ops/actions/workflows/test.yml/badge.svg)](https://github.com/Larryveryhandsome/tw-service-ops/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)

給「**預約制、要派人出去服務**」的店家用的營運系統：客人線上預約 → 老闆確認報價 → 排班派工 → 服務人員手機打卡拍照 → 月底帳單與薪資。
到府維修、美容美髮、家教課程、搬家、汽機車保養、工作室……只要是「客人約時間、店家派人」的生意都適用。

> 這是一套從實際營運系統抽出來、去掉行業與品牌的**示範版原始碼**。下載後一行指令就能在自己電腦上跑起來試玩，資料全部是虛構的，預設不會連到 LINE、金流或任何外部服務。

## 這解決什麼問題

| 環節 | 傳統做法的痛點 | 這套系統的做法 |
|---|---|---|
| ① 接詢問與報價 | 客人在 LINE、電話一直問「多少錢」「哪天有空」；報價寫在對話裡，漏回、報錯價，事後查不到 | 客人在預約頁自己選項目、數量、日期時段，系統先試算；報價進老闆的「報價」清單，確認後才傳出，客人用網址看正式報價單、線上確認 |
| ② 預約排程 | 行程記在紙本、白板或老闆腦袋裡，人一多就重複約、記錯時間；改時間要來回傳訊息 | 客人確認報價後自動變成工單；排班頁一次看每天每場的時間、地址、人員；客人可從網址申請改時間，店家確認後才生效 |
| ③ 派工與臨時請假 | 一場一場打電話問誰有空；有人臨時請假，老闆整天找人補 | 派工頁列出缺人的工作，依服務區域、可排時間挑人，對方在手機接受或婉拒；請假的那一場回到「待補人」 |
| ④ 現場回報與照片 | 照片散在每個人手機和群組；幾點到、幾點走只能口頭說 | 人員手機開工作頁：到場打卡、服務前／完成照片、完成回報、離場打卡，全部跟著工單存檔 |
| ⑤ 回訪與再次預約 | 做完就沒下文；報價傳出去客人沒回也沒人追 | 客戶資料集中（歷次服務、地點、付款方式、評價）；報價狀態一覽，看得出哪張還沒確認 |
| ⑥ 月底對帳與薪資 | 月底翻 LINE、Excel 一場一場對；工時靠自己記，薪資容易算錯 | 結帳頁依完成的工單整理月結帳單與收款；人員薪資依工時與場次彙整，人員在手機看得到自己的收入 |
| ⑦ 老闆不在也掌握全局 | 客服、帳務、現場都要問老闆 | 老闆、客服、帳務、服務人員各有權限，只看自己需要的畫面；地區分布圖顯示各縣市客戶數與可支援人員 |

實際效果依各店流程而定，原則上需要依你的作法調整設定。

## 角色與權限

| 角色（示範帳號） | 看得到 | 看不到 |
|---|---|---|
| 管理員 `admin` | 全部：報價、排班、派工、客戶、帳務、設定 | — |
| 客服 `staff` | 報價、排班、派工、客戶 | 帳務、薪資、記帳 |
| 帳務 `finance` | 帳單、收款、薪資、記帳 | 系統設定 |
| 服務人員 `worker` | 自己的班表、可排時間、自己的收入 | 別人的工作、電話、收入；所有管理 API（回 403） |

## 安裝與執行（約 3 分鐘）

需要 Python 3.10 以上（Windows、macOS、Linux 都可以跑示範版）。

```bash
git clone https://github.com/Larryveryhandsome/tw-service-ops.git
cd tw-service-ops
python -m venv .venv
# Windows：.venv\Scripts\activate　　macOS／Linux：source .venv/bin/activate
pip install -r requirements.txt

python app/seed_demo.py      # 建立虛構示範資料（放在 data/，不進 git）
python app/demo_server.py    # 啟動示範：http://127.0.0.1:5490
```

打開 <http://127.0.0.1:5490>，先看說明頁，再按「直接試玩」。
頂部綠色列可以隨時切換「老闆／客服／帳務／服務人員／客人」角色。

- 免登入入口：`/enter?as=admin`（也可以 `staff`、`finance`、`worker`），加 `&go=quotes` 直接打開某一頁（例如 `quotes`、`board`）。
- 停止：`python app/demo_server.py --stop`（只接受本機直連的 `/__demo_stop`，經過反向代理或外部網域一律 404）。
- 換埠：`DEMO_PORT=5600 python app/demo_server.py`；換資料夾：`OPS_DATA=/path/to/data`。
- 想重來：再跑一次 `python app/seed_demo.py`（舊的資料庫會改名保留在 `data/`，不會刪）。
- 背景執行（Windows 不跳黑色視窗）：`pythonw app/demo_server.py`，輸出會寫到 `data/logs/demo_server.log`。

3 分鐘試玩順序：當客人在 `/book` 送一筆預約 → 當老闆在「報價」確認 → 回到客人看正式報價單 → 看排班、派工 → 切到服務人員看手機畫面 → 看月結帳單與薪資。

## 預設全部關閉：LINE、金流、推播、AI

`demo_server.py` 啟動時會：

- 設定 `OPS_NO_PUSH=1`：不發 LINE 訊息、不送手機推播、不連 LINE Pay、不連 Google 日曆、不叫 AI。
- **封鎖所有對外連線**：除了本機示範埠，任何連線（含 DNS 查詢）都會被拒絕，保證不會不小心連到外部服務。
- 只綁 `127.0.0.1`，同網路的其他電腦連不到。

要串接真正的 LINE 官方帳號、LINE Pay、Google 日曆、Anthropic（AI 記帳／對話重點），請看 [`docs/金鑰申請.md`](docs/金鑰申請.md)：怎麼自己申請、填在哪裡。
**本 repo 不附任何金鑰**；所有金鑰都從環境變數 `OPS_SECRET_<名稱>` 讀，預設空值。

## 專案結構

```
app/
  demo_server.py   示範啟動器（網路防護、免登入 /enter、/__demo_stop）
  seed_demo.py     建立虛構示範資料
  server.py        主伺服器（標準函式庫 http.server，只綁 127.0.0.1）
  core.py          資料庫（SQLite）、稽核紀錄、共用計算
  booking.py       預約 → 報價 → 簽收 → 工單
  dispatch.py      派工、補人
  board.py         排班、工單、客戶合併
  worker_portal.py 服務人員手機頁、應徵
  payments.py ledger.py  收款、帳單、記帳
  line_bot.py      LINE 官方帳號（示範版不連線）
  schema*.sql      資料表
  web/             前端（純 HTML／JS／CSS，不需要 build）
tests/             端到端測試（seed、server、權限、預約、對外封鎖）
docs/金鑰申請.md    串接外部服務的申請教學
```

## 測試

```bash
python -m unittest discover -s tests -v
```

每個測試都在暫存資料夾建資料、用隨機埠啟動自己的示範 server，跑完自己停掉；不連網、不需要任何金鑰。
涵蓋：seed 成功、server 起得來、主要頁面 200、免登入 `/enter`（含 `go` 參數與不安全值）、`/__demo_stop` 只接受本機、對外連線被擋、`/book` 預約送出並成立報價、權限（服務人員看不到別人的資料、各角色不能用越權 API）、重新應徵不能冒用他人身分、備份後實際還原起一個 server、金鑰只從環境變數讀。
GitHub Actions 會在 Python 3.10 與 3.12 自動跑。

## ⚠️ 已知限制

- **這是示範版**：適合試玩、評估、當成客製化的起點；直接拿去正式營運前，原則上需要依你的流程調整（服務項目、價目、人員規則、帳號建立方式）。
- **LINE、LINE Pay、Google 日曆、Anthropic、Web Push 的串接都沒有在這個公開版用真實帳號測過**（示範版預設全關）。第一次串接請先用 LINE Pay 沙盒、測試用官方帳號。
- **在畫面上儲存金鑰需要 Windows**（用 Windows DPAPI 加密存進資料庫）；Linux／macOS 請改用環境變數 `OPS_SECRET_<名稱>`。
- 介面、訊息、說明全部是**繁體中文**，地址、行政區、統編、LINE 等設計都以**台灣**為準。
- 報價單 **PDF 下載**（`/q/<token>.pdf`）與官網的伺服器端預先渲染需要額外的工具程式，**未包含在公開版**；報價單請用網頁版或瀏覽器列印。
- 正式（非示範）模式沒有附「建立第一個管理員帳號」的指令，示範版以外的部署原則上需要客製化。
- 單機 SQLite＋單一行程，適合小店（數人到數十人）規模；沒有做多機部署。
- 部分功能的英文程式識別字沿用原本的命名（例如服務人員角色代碼 `cleaner`），不影響使用。
- 程式從實際營運系統去識別化而來，註解裡仍有一些「當初為什麼這樣設計」的說明，已改寫成中性的技術說明。
- 免登入入口 `/enter` 與 `/__demo_stop` 只存在於 `demo_server.py`，**不要把示範版直接開到網路上**當正式系統用。

## 授權

[Apache-2.0](LICENSE)。轉散布時請保留 [NOTICE](NOTICE) 的署名與畫面底部的客製化廣告列。資安問題請看 [SECURITY.md](SECURITY.md)。

本專案只是工具，原則上不構成法律、稅務或勞動法規意見；使用者應自行確認符合《個人資料保護法》、《勞動基準法》與各平台條款。

---

## 🛠️ 需要客製化？

這個工具是開源的，可以免費使用。如果你想要：

- 改成你們店的服務項目、價目、派工與薪資規則
- 串接你現在在用的系統（LINE 官方帳號、LINE Pay、Google 日曆、記帳、官網……）
- 幫你安裝、申請金鑰、上線部署、教學，或長期維護

歡迎加 LINE 跟我們聊。我們會依需求的範圍與複雜度報價，原則上以實際報價單為準。

<a href="https://lin.ee/pdYGARu"><img src="https://scdn.line-apps.com/n/line_add_friends/btn/zh-Hant.png" alt="加入好友" height="36"></a>

- 💬 LINE 官方帳號：[@812ibtir](https://lin.ee/pdYGARu)（請註明「店家營運中樞 客製化」）
- 🏢 留白事務所：網頁設計、AI 自動化、房仲遠端助理
- 👤 作者：[留白事務所](https://github.com/Larryveryhandsome)
