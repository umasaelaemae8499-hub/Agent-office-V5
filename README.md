# Japan-travel-website

**JapanOS** — a Unix-style terminal that runs in the browser, built for iPhone.

`index.html` is fully self-contained (no dependencies, no build step, no
server). It boots a shell called `jsh` with a real virtual filesystem; the
Japan travel guides live in `~/guides` as plain text files you `cat`, `grep`,
and pipe like on a PC.

## The shell

- **Filesystem**: `ls`, `cd`, `pwd`, `cat`, `mkdir`, `touch`, `rm`, `rmdir`,
  `mv`, `cp`, `tree`, `head`, `tail`, `wc`, `grep`
- **Pipes & redirection**: `cat guides/food.txt | grep ramen`,
  `echo 'note' > plans/todo.txt`, `>>` to append
- **Tab completion** for commands and paths; **command history** (↑/↓)
- **Text editor**: `edit file.txt` (also answers to `nano`, `vi`, `vim`)
- **Persistence**: files you create are saved in localStorage on your device
  and survive closing the app; `reset` restores factory state
- **Extras**: `neofetch`, `cowsay`, `fortune` (omikuji), `man`, `sudo` (try it)

Everything runs client-side. Browsers are sandboxed, so this is a shell
*emulator* — it cannot touch the phone's real OS (nothing can, from a web
page). For a genuinely real Linux shell on iPhone, see the free App Store
apps **iSH** or **a-Shell**.

## Use it on your iPhone

1. Host the site (GitHub Pages: Settings → Pages → deploy from branch),
   then open the URL in Safari.
2. Tap **Share → Add to Home Screen**.
3. Launch from your home screen — it runs full-screen like a native app.

## iPhone-specific touches

- Standalone home-screen mode, safe-area insets for the Dynamic Island
  and home indicator
- Layout tracks the visual viewport so the keyboard never covers the prompt
- 16px input font so iOS Safari never zooms on focus
- Key bar with the characters the iOS keyboard hides: `tab` `|` `>` `~` `/`
  plus ↑/↓ history and `^C`
