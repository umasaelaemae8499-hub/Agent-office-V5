/**
 * Affiliate partner registry for the Japan travel website.
 *
 * Each partner entry defines how outbound links to that partner are
 * rewritten into affiliate links. `param` is the query parameter the
 * partner uses for the affiliate ID, and `affiliateId` is our ID with
 * that program (placeholder values until real program IDs are issued).
 */
const AFFILIATE_PARTNERS = [
  {
    id: "jrpass",
    name: "Japan Rail Pass",
    category: "Transport",
    domains: ["jrailpass.com", "japan-rail-pass.com"],
    param: "aff",
    affiliateId: "JTW-1001",
    commission: 0.08,
    description: "Nationwide JR Pass and regional rail passes.",
  },
  {
    id: "klook",
    name: "Klook",
    category: "Tours & Activities",
    domains: ["klook.com"],
    param: "aid",
    affiliateId: "JTW-1002",
    commission: 0.05,
    description: "Tours, activities, attraction tickets and pocket wifi.",
  },
  {
    id: "booking",
    name: "Booking.com",
    category: "Hotels",
    domains: ["booking.com"],
    param: "aid",
    affiliateId: "JTW-1003",
    commission: 0.04,
    description: "Hotels and ryokan across Japan.",
  },
  {
    id: "agoda",
    name: "Agoda",
    category: "Hotels",
    domains: ["agoda.com"],
    param: "cid",
    affiliateId: "JTW-1004",
    commission: 0.045,
    description: "Hotels with strong Asia coverage.",
  },
  {
    id: "gettyourguide",
    name: "GetYourGuide",
    category: "Tours & Activities",
    domains: ["getyourguide.com"],
    param: "partner_id",
    affiliateId: "JTW-1005",
    commission: 0.08,
    description: "Guided tours and day trips.",
  },
  {
    id: "sakuramobile",
    name: "Sakura Mobile",
    category: "Connectivity",
    domains: ["sakuramobile.jp"],
    param: "ref",
    affiliateId: "JTW-1006",
    commission: 0.1,
    description: "SIM cards and pocket wifi rental for Japan.",
  },
];

function findPartnerForUrl(url) {
  let host;
  try {
    host = new URL(url, window.location.href).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
  return (
    AFFILIATE_PARTNERS.find((p) =>
      p.domains.some((d) => host === d || host.endsWith("." + d))
    ) || null
  );
}

if (typeof module !== "undefined") {
  module.exports = { AFFILIATE_PARTNERS, findPartnerForUrl };
}
