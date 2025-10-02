// Tiny pop-up helper for Sign-in Reward
// Works with your existing button/submit flow (no view changes).
// Shows exactly 3 messages:
//  - Not eligible: "Complete more tasks to get reward." (blocks action)
//  - Already claimed: "You already claimed today." (blocks action)
//  - Success (after redirect): "Congratulations! You received €X." (+ €350 on Day 5)

(function () {
  const REWARDS = [10, 30, 50, 100, 200]; // Day 1..5 (€)
  const CLAIM_KEY = "sr_claim_intent";

  const $ = (s, r) => (r || document).querySelector(s);

  // ----- Minimal toast UI (injected, no CSS file) -----
  function ensureToastUI() {
    if ($("#sr-toast")) return;
    const style = document.createElement("style");
    style.textContent = `
      #sr-toast{
        position:fixed; left:50%; bottom:20px; transform:translateX(-50%);
        background:#111827; color:#fff; padding:12px 16px; border-radius:10px;
        box-shadow:0 10px 24px rgba(0,0,0,.2); font-size:14px; line-height:1.4;
        z-index:9999; opacity:0; pointer-events:none; transition:opacity .2s ease, transform .2s ease;
      }
      #sr-toast.show{ opacity:1; transform:translateX(-50%) translateY(0); }
      #sr-toast.success{ background:#065f46; }  /* green */
      #sr-toast.warn{ background:#92400e; }     /* amber */
    `;
    const div = document.createElement("div");
    div.id = "sr-toast";
    div.setAttribute("aria-live", "polite");
    div.setAttribute("aria-atomic", "true");
    document.head.appendChild(style);
    document.body.appendChild(div);
  }

  function showToast(text, kind = "success", ms = 3500) {
    ensureToastUI();
    const t = $("#sr-toast"); if (!t) return;
    t.className = kind === "success" ? "success" : "warn";
    t.textContent = text;
    requestAnimationFrame(() => {
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), ms);
    });
  }

  // ----- Read state from hidden block -----
  function readState() {
    const data = $("#sr-data");
    if (!data) return null;
    return {
      streak: parseInt(data.dataset.streak || "0", 10),          // 0..5
      canClaim: data.dataset.canClaim === "1",
      claimedToday: data.dataset.claimedToday === "1",
      nextReward: parseInt(data.dataset.next || "0", 10)         // € integer
    };
  }

  // ----- Claim intent memory (so success can show after redirect) -----
  function rememberClaimIntent(streak) {
    if (Number.isFinite(streak)) {
      localStorage.setItem(CLAIM_KEY, JSON.stringify({
        streakBefore: streak,
        at: Date.now()
      }));
    }
  }

  // Identify claim triggers without view changes
  function isClaimTrigger(el) {
    if (!el) return false;
    if (el.closest("[data-sr-claim]")) return true;
    if (el.closest(".sr-btn")) return true;
    const a = el.closest("a, button, input[type=submit]");
    if (!a) return false;
    const href = (a.getAttribute("href") || a.formAction || "").toLowerCase();
    return href.includes("signinreward") && href.includes("claim");
  }

  // ----- Click & submit handlers -----
  function onClick(e, st) {
    const target = e.target;
    if (!isClaimTrigger(target)) return;

    // If already claimed OR not eligible → block and show popup
    if (st.claimedToday) {
      e.preventDefault(); e.stopPropagation();
      showToast("You already claimed today.", "warn");
      return;
    }
    if (!st.canClaim) {
      e.preventDefault(); e.stopPropagation();
      showToast("Complete more tasks to get reward.", "warn");
      return;
    }

    // Eligible → let system handle it, but remember for success toast after redirect
    rememberClaimIntent(st.streak);
  }

  function onSubmit(e, st) {
    try {
      const method = (e.target.method || "").toLowerCase();
      const action = (e.target.action || "").toLowerCase();
      if (method === "post" && action.includes("signinreward") && action.includes("claim")) {
        if (st.claimedToday) {
          e.preventDefault(); e.stopPropagation();
          showToast("You already claimed today.", "warn");
        } else if (!st.canClaim) {
          e.preventDefault(); e.stopPropagation();
          showToast("Complete more tasks to get reward.", "warn");
        } else {
          rememberClaimIntent(st.streak);
        }
      }
    } catch (_) {}
  }

  // ----- After redirect: show success if streak advanced -----
  function maybeShowSuccess(st) {
    const raw = localStorage.getItem(CLAIM_KEY);
    if (!raw) return;
    localStorage.removeItem(CLAIM_KEY);

    let info = null;
    try { info = JSON.parse(raw); } catch (_) {}
    if (!info || Date.now() - (info.at || 0) > 2 * 60 * 1000) return; // 2 min expiry

    const before = parseInt(info.streakBefore ?? -1, 10);
    const after = st.streak;
    if (!Number.isFinite(before) || !Number.isFinite(after)) return;

    if (after === before + 1) {
      const dayIndex = before; // 0-based day just claimed
      if (dayIndex === 4) {
        showToast(`Congratulations! You received €${REWARDS[4]} + €350 bonus.`, "success");
      } else if (dayIndex >= 0 && dayIndex < REWARDS.length) {
        showToast(`Congratulations! You received €${REWARDS[dayIndex]}.`, "success");
      } else if (st.nextReward > 0) {
        showToast(`Congratulations! You received €${st.nextReward}.`, "success");
      } else {
        showToast("Congratulations! You received a reward.", "success");
      }
    }
  }

  function init() {
    const st = readState();
    if (!st) return;

    document.addEventListener("click", (e) => onClick(e, st), true);
    document.addEventListener("submit", (e) => onSubmit(e, st), true);
    maybeShowSuccess(st);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
