# Atlas Design System v2

Centralized tokens live in `frontend/src/styles.css` (`:root` + `[data-theme="dark"]`).
Shared behavior primitives live in `frontend/src/ui.tsx`.

## Tokens
- Surfaces: `--bg / --bg-sunken / --surface / --surface-2 / --surface-hover / --surface-active / --surface-selected`
- Text: `--ink / --ink-2 / --ink-3 / --muted / --faint`
- Lines: `--line / --line-strong / --divider`
- Accent: `--accent (+hover/active/soft/line/ink)` — same hue both themes, retuned for contrast on dark
- Status: `--ok / --err / --warn (+soft/line)` — never rely on color alone; pair with icon + text
- Elevation: `--shadow-1/2/3`; overlay `--overlay`
- Radius: `--r-xs 6 / --r-sm 8 / --r-md 10 / --r-lg 14 / --r-xl 18 / --r-full`
- Motion: `--dur-instant 80 / --dur-fast 140 / --dur-med 200 / --dur-slow 300`, `--ease-out / --ease-std`
- Type: `--fs-11…28`, `--lh-tight/body/loose`; UI `--font-ui`, code `--font-mono`
- Focus: `--focus-ring` via `:focus-visible` everywhere
- z: `--z-pop 60 / --z-drawer 80 / --z-modal 140 / --z-toast 220 / --z-ctx 300`

## Rules
- Components use tokens only. No hex/rgb outside the token blocks.
- Buttons: `.btn (+.primary/.danger/.ghost/.subtle/.sm/.xs/.block)`; icon actions `.icon-btn (+.bordered/.danger)`; inline `.mini-btn`; text `.linklike`. Stable width on loading; `:disabled` dims + `not-allowed`.
- Inputs: `.text-input` or native with token styles; hover darkens border, focus = accent border + ring; `aria-invalid` → err border + soft bg; never placeholder-only labels (pair with `<label>` or `aria-label`).
- Feedback: `useToast()` instead of `alert()`; `useConfirm()` instead of `confirm()`. Toasts bottom-right, max 5, errors persist 7s. `role=status` / `role=alertdialog` semantics built in.
- Loading: `.skel` shimmer for known layouts (`<Skeleton/>`), `.spin` for inline ops, phase text ("Queued…/Thinking…") for agent waits — never fake percentages.
- Motion: opacity + ≤12px translate only; pop/dialog/slide keyframes; `prefers-reduced-motion` disables all non-essential motion.
- Themes: toggle in sidebar persists `atlas.theme`; defaults to OS preference. Dark theme is retuned, not inverted.

## Remaining limitations
- Jira/Tasks/Notes keep their own layout classes but now share tokens; deeper structural unification (single DataTable primitive) is future work.
- No virtualized lists yet; fine at current data volumes.
- `OnboardingWizard` / `MemoryView` copy untouched functionally; visually covered by modal/tab/button tokens.
