# Affiliate System

A self-contained, dependency-free affiliate link management and tracking
system for the Japan travel website. Plain HTML/CSS/JS — no build step,
no backend required.

## What it does

- **Partner registry** (`js/partners.js`) — the affiliate programs we
  belong to (JR Pass, Klook, Booking.com, Agoda, GetYourGuide, Sakura
  Mobile), each with its tracking query parameter, our affiliate ID and
  the commission rate.
- **Link decoration** (`js/affiliate-tracker.js`) — on page load, every
  outbound link to a known partner domain is rewritten to carry our
  affiliate ID plus UTM campaign tags, and gets `rel="sponsored"`.
  Opt a link out with the `data-no-affiliate` attribute.
- **Inbound referral capture** — visitors arriving with `?ref=CODE` are
  remembered for 30 days (last-click attribution) and the code is
  attached to any affiliate clicks they make.
- **Click tracking** — outbound affiliate clicks are recorded to
  `localStorage`. There is no server; replace `persistClick()` in
  `affiliate-tracker.js` with an API call when a backend exists.
- **Dashboard** (`index.html`) — click totals, per-partner stats with
  estimated revenue, a recent-clicks table and an affiliate link
  generator.
- **Demo page** (`demo.html`) — a sample travel article whose partner
  links are decorated and tracked, for trying the system end to end.

## Try it

Open the folder with any static server and visit the dashboard:

```bash
cd Projects/Affiliate-System
python3 -m http.server 8000
# http://localhost:8000            → dashboard
# http://localhost:8000/demo.html  → demo article (click links to record data)
```

## Using it on a real page

Include the two scripts on any page of the site; links are decorated
automatically:

```html
<script src="/Projects/Affiliate-System/js/partners.js"></script>
<script src="/Projects/Affiliate-System/js/affiliate-tracker.js"></script>
```

To add a partner, append an entry to `AFFILIATE_PARTNERS` in
`js/partners.js` with the partner's domains, affiliate query parameter
and your program ID. The affiliate IDs currently in the registry are
placeholders (`JTW-10xx`) — replace them with real IDs once the partner
programs are approved.

## Notes

- Revenue figures on the dashboard are estimates
  (clicks × 3% assumed conversion × ¥12,000 assumed basket × commission);
  tune the constants at the top of `js/dashboard.js`.
- All data is per-browser (`localStorage`), which is fine for demos and
  local testing but means production reporting needs a backend.
