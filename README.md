# Japan-travel-website

A Japan travel guide styled as a retro terminal — built for iPhone.

## ⛩ Japan Travel Terminal

`index.html` is a self-contained web terminal (no dependencies, no build step).
Type or tap commands to explore destinations, itineraries, food, phrases,
budgets, and more.

### Commands

`help` · `destinations` · `itinerary` · `food` · `phrases` · `tips` ·
`seasons` · `budget` · `transport` · `fortune` · `date` · `echo` · `clear`

### Use it on your iPhone

1. Host the site (GitHub Pages works great: Settings → Pages → deploy from
   branch), or open `index.html` any other way in Safari.
2. In Safari, tap **Share → Add to Home Screen**.
3. Launch it from your home screen — it runs full-screen like a native app,
   with safe-area support for the notch and home indicator.

### iPhone-specific touches

- Full-screen standalone mode (`apple-mobile-web-app-capable`)
- Safe-area insets for notch / Dynamic Island / home indicator
- 16px input font so iOS Safari never zooms on focus
- Layout tracks the visual viewport so the keyboard doesn't cover the prompt
- Tap-friendly command chips + on-screen enter key
- Command history with arrow keys when a hardware keyboard is attached
