/**
 * Renders the affiliate dashboard: summary tiles, per-partner stats and
 * a recent-clicks table, all computed from clicks recorded by
 * affiliate-tracker.js in localStorage.
 */
(() => {
  const AVG_ORDER_VALUE_JPY = 12000; // assumed basket for revenue estimates
  const CONVERSION_RATE = 0.03; // assumed clicks -> purchases

  const yen = new Intl.NumberFormat("ja-JP", {
    style: "currency",
    currency: "JPY",
  });

  function statsByPartner(clicks) {
    const stats = new Map();
    for (const partner of AFFILIATE_PARTNERS) {
      stats.set(partner.id, { partner, clicks: 0 });
    }
    for (const click of clicks) {
      const entry = stats.get(click.partnerId);
      if (entry) entry.clicks += 1;
    }
    return [...stats.values()].sort((a, b) => b.clicks - a.clicks);
  }

  function estimatedRevenue(clicks, commission) {
    return clicks * CONVERSION_RATE * AVG_ORDER_VALUE_JPY * commission;
  }

  function renderSummary(clicks) {
    const total = clicks.length;
    const revenue = statsByPartner(clicks).reduce(
      (sum, s) => sum + estimatedRevenue(s.clicks, s.partner.commission),
      0
    );
    const last7 = clicks.filter(
      (c) => Date.now() - c.timestamp < 7 * 86400000
    ).length;

    document.getElementById("stat-clicks").textContent = total;
    document.getElementById("stat-week").textContent = last7;
    document.getElementById("stat-partners").textContent =
      AFFILIATE_PARTNERS.length;
    document.getElementById("stat-revenue").textContent = yen.format(
      Math.round(revenue)
    );
  }

  function renderPartnerTable(clicks) {
    const tbody = document.querySelector("#partner-table tbody");
    tbody.innerHTML = "";
    for (const { partner, clicks: count } of statsByPartner(clicks)) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>
          <strong>${partner.name}</strong>
          <span class="muted">${partner.description}</span>
        </td>
        <td><span class="badge">${partner.category}</span></td>
        <td><code>${partner.affiliateId}</code></td>
        <td>${(partner.commission * 100).toFixed(1)}%</td>
        <td class="num">${count}</td>
        <td class="num">${yen.format(
          Math.round(estimatedRevenue(count, partner.commission))
        )}</td>`;
      tbody.appendChild(row);
    }
  }

  function renderRecentClicks(clicks) {
    const tbody = document.querySelector("#clicks-table tbody");
    tbody.innerHTML = "";
    const recent = [...clicks].reverse().slice(0, 20);
    if (recent.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="muted">No clicks recorded yet — open the demo page and click some partner links.</td></tr>';
      return;
    }
    for (const click of recent) {
      const partner = AFFILIATE_PARTNERS.find((p) => p.id === click.partnerId);
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${new Date(click.timestamp).toLocaleString()}</td>
        <td>${partner ? partner.name : click.partnerId}</td>
        <td>${click.page}</td>
        <td>${click.referral ?? "—"}</td>`;
      tbody.appendChild(row);
    }
  }

  function renderLinkGenerator() {
    const form = document.getElementById("link-generator");
    const output = document.getElementById("generated-link");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const rawUrl = form.elements.url.value.trim();
      const partner = findPartnerForUrl(rawUrl);
      if (!partner) {
        output.textContent =
          "No affiliate program matches that domain. Add the partner in partners.js first.";
        return;
      }
      output.textContent = AffiliateTracker.buildAffiliateUrl(rawUrl, partner);
    });
  }

  function render() {
    const clicks = AffiliateTracker.getClicks();
    renderSummary(clicks);
    renderPartnerTable(clicks);
    renderRecentClicks(clicks);
  }

  document.getElementById("clear-data").addEventListener("click", () => {
    AffiliateTracker.clearClicks();
    render();
  });

  renderLinkGenerator();
  render();
})();
