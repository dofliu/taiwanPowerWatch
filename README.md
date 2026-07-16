# 台灣電力情報站 · Taiwan Power Watch

台灣**全能源別**即時電力資訊：燃氣、燃煤、核能、汽電共生、風力、太陽能、水力、抽蓄、燃油、儲能、
其他再生能源——即時發電結構、各能源別出力與全國備轉容量率，資料全部來自政府公開資料 API。

- Web 版：`https://dofliu.github.io/taiwanPowerWatch/`
- Android App：Capacitor 打包（見下方「建置 App」）

姊妹專案：[風電風情 windfarmTaiwan](https://github.com/dofliu/windfarmTaiwan)（逐風場深度資訊，維持獨立運作）。
本專案沿用其已實測驗證的資料管線模式（scraper → JSON → 靜態前端）與誠實原則。

## 架構

```
GitHub Actions (每 15 分 cron) ── taipower_power_scraper.py ──► docs/*.json ──┐
                                                                              ├─► commit 回 repo
GitHub Pages 服務 docs/：index.html + JSON ◄──────────────────────────────────┘
Web：同網域 fetch ／ Android App(Capacitor)：fetch Pages 絕對網址(Pages 回應 CORS *)
```

## 檔案

- `taipower_power_scraper.py` — 抓台電 genary（含**全部**機組），依機組類型分組彙總；
  同時抓電力供需（備轉容量率，主要來源被 WAF 擋時退每日備援並誠實標示）
- `docs/index.html` — 行動優先前端（發電結構堆疊條、各能源別卡片＋24h 趨勢、備轉燈號）
- `docs/power_realtime.json` — 即時各能源別彙總＋原始機組類型對照（透明可校準）
- `docs/power_history.json` — 滾動 7 天歷史
- `docs/grid_status.json` — 電力供需
- `capacitor.config.json` / `package.json` — Capacitor App 設定（webDir 指向 docs/）
- `.github/workflows/scrape.yml` — 每 15 分鐘資料更新
- `.github/workflows/android-build.yml` — 手動觸發建置 debug APK（android/ 平台目錄由 CI 即時產生，不進版控）

## 啟用步驟

1. Settings → Pages → Source 選 `main` / `docs` 資料夾，存檔。
2. Actions 手動跑一次 `scrape-taipower-power`，確認 `docs/power_realtime.json` 被 commit。
3. 開 `https://dofliu.github.io/taiwanPowerWatch/`。

## 建置 Android App

**免本機環境（推薦先用）**：Actions 手動跑 `android-build`，完成後在 run 的 Artifacts 下載
`taiwanPowerWatch-debug-apk`，手機安裝（需允許未知來源）。

**本機開發**：`npm install && npx cap add android && npx cap sync android`，
再用 Android Studio 開 `android/`。上架 Google Play 需開發者帳號（一次性 US$25）與正式簽章。

## 資料來源與授權

- 政府資料開放平臺「台灣電力公司各機組發電量即時資訊」（[資料集 8931](https://data.gov.tw/dataset/8931)），每 10 分更新
- 台電電力供需資訊（備援：[資料集 19995](https://data.gov.tw/dataset/19995)）
- 政府資料開放授權條款－第 1 版
- 抽蓄/儲能之「負載」為負值照實呈現；分組對照表見 scraper 的 `GROUP_RULES`，
  未知類型歸入「其他」並於 log 揭露原始名稱，不寫入猜測值
