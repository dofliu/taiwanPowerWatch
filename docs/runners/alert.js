// 供電警戒背景檢查器(Capacitor Background Runner，Android 由 WorkManager 每 ~30 分執行一次)。
// 誠實原則：資料若為每日備援(granularity=daily，可能落後數週)，通知文字會註明「非即時」。
// 只在燈號「惡化進入」警戒(<6%)或限電(<3%)時通知一次，恢復不打擾。

addEventListener("setEnabled", (resolve, reject, args) => {
  try {
    CapacitorKV.set("alerts_enabled", args && args.enabled ? "1" : "0");
    resolve();
  } catch (e) { reject(e); }
});

addEventListener("checkGrid", async (resolve, reject) => {
  try {
    let enabled = "0";
    try { enabled = (CapacitorKV.get("alerts_enabled") || {}).value || "0"; } catch (_) {}
    if (enabled !== "1") { resolve(); return; }

    const res = await fetch("https://dofliu.github.io/taiwanPowerWatch/grid_status.json");
    const j = await res.json();
    const p = j.reserve_pct;
    if (typeof p !== "number") { resolve(); return; }

    const tier = p < 3 ? "limit" : p < 6 ? "warn" : "ok";
    let last = "ok";
    try { last = (CapacitorKV.get("last_tier") || {}).value || "ok"; } catch (_) {}

    if ((tier === "warn" || tier === "limit") && tier !== last) {
      const label = tier === "limit" ? "限電警戒" : "供電警戒";
      const daily = j.granularity === "daily" ? "（官方每日資料，非即時）" : "";
      CapacitorNotifications.schedule([{
        id: Date.now() % 2147483647,
        title: "⚡ " + label,
        body: `全國備轉容量率 ${p.toFixed(1)}%${daily}`,
      }]);
    }
    CapacitorKV.set("last_tier", tier);
    resolve();
  } catch (e) { reject(e); }
});
