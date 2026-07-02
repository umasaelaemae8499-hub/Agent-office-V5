/**
 * Affiliate tracking library.
 *
 * Responsibilities:
 *  1. Capture inbound referral codes (?ref=CODE) and remember who referred
 *     the visitor for ATTRIBUTION_DAYS (last-click attribution).
 *  2. Rewrite outbound links to known partners so they carry our
 *     affiliate ID (see partners.js).
 *  3. Record every outbound affiliate click locally so the dashboard can
 *     report clicks per partner. Storage is localStorage only — there is
 *     no backend; swap `persistClick` for an API call when one exists.
 *
 * Usage: include partners.js then this file on any page. Links are
 * decorated automatically on DOMContentLoaded. Opt out of rewriting a
 * specific link with `data-no-affiliate`.
 */
const AffiliateTracker = (() => {
  const CLICKS_KEY = "jtw_affiliate_clicks";
  const REFERRAL_KEY = "jtw_referral";
  const ATTRIBUTION_DAYS = 30;

  function readJson(key, fallback) {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  }

  function writeJson(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  /* ---- inbound referral capture ---- */

  function captureReferral() {
    const code = new URLSearchParams(window.location.search).get("ref");
    if (!code) return;
    writeJson(REFERRAL_KEY, {
      code,
      capturedAt: Date.now(),
      landingPage: window.location.pathname,
    });
  }

  function getReferral() {
    const ref = readJson(REFERRAL_KEY, null);
    if (!ref) return null;
    const ageDays = (Date.now() - ref.capturedAt) / 86400000;
    if (ageDays > ATTRIBUTION_DAYS) {
      localStorage.removeItem(REFERRAL_KEY);
      return null;
    }
    return ref;
  }

  /* ---- outbound link decoration ---- */

  function buildAffiliateUrl(rawUrl, partner) {
    const url = new URL(rawUrl, window.location.href);
    url.searchParams.set(partner.param, partner.affiliateId);
    // Standard campaign tags so partner-side reports show the source page.
    url.searchParams.set("utm_source", "japan-travel-website");
    url.searchParams.set("utm_medium", "affiliate");
    url.searchParams.set("utm_campaign", window.location.pathname);
    return url.toString();
  }

  function decorateLinks(root = document) {
    root.querySelectorAll("a[href]").forEach((a) => {
      if (a.hasAttribute("data-no-affiliate")) return;
      if (a.dataset.affiliatePartner) return; // already decorated
      const partner = findPartnerForUrl(a.href);
      if (!partner) return;
      a.href = buildAffiliateUrl(a.href, partner);
      a.dataset.affiliatePartner = partner.id;
      a.rel = "sponsored noopener";
      a.target = a.target || "_blank";
    });
  }

  /* ---- click recording ---- */

  function persistClick(click) {
    const clicks = readJson(CLICKS_KEY, []);
    clicks.push(click);
    writeJson(CLICKS_KEY, clicks);
  }

  function onDocumentClick(event) {
    const link = event.target.closest("a[data-affiliate-partner]");
    if (!link) return;
    persistClick({
      partnerId: link.dataset.affiliatePartner,
      url: link.href,
      page: window.location.pathname,
      referral: getReferral()?.code ?? null,
      timestamp: Date.now(),
    });
  }

  function getClicks() {
    return readJson(CLICKS_KEY, []);
  }

  function clearClicks() {
    localStorage.removeItem(CLICKS_KEY);
  }

  function init() {
    captureReferral();
    decorateLinks();
    document.addEventListener("click", onDocumentClick);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { decorateLinks, getClicks, clearClicks, getReferral, buildAffiliateUrl };
})();
