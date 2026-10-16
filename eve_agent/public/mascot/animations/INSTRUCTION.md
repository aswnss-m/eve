# Eve mascot animations

Eve's face. Keep this file current whenever the mascot's files, crop or placement change.

## Files

- One clip = `<name>.mp4` + `<name>.gif` with the same base name. Today:
  - `idle_animation`: 6 s loop, the rest state.
  - `eve_searching`: 4 s intro, starts at the rest face, a laptop rises, then she types. Played once when
    Eve starts working.
  - `eve_searching_looped`: 3 s typing loop, must start and end on the intro's last frame. Loops while Eve
    is working, after the intro.
  - `eve_searching_reversed`: the intro backwards, played once when the work ends, then back to rest.
- Square frame, face centred, plain white background (the UI crops to a circle and scales the frame 1.15x, so the
  whole head must fit inside the circle inscribed in the square, with a little room; nothing near the edges).
- MP4: H.264, yuv420p, loopable (last frame flows into the first), **no audio track** (the current idle clip has
  one, so do the searching clips; they are muted in the UI, drop it on the next export). The GIF is the fallback for browsers that cannot play
  the video, so it can be smaller (the 480px one is 2.4 MB; the 720px MP4 is 0.4 MB).
- `../idle_no_background.webp` (512 px, transparent, still): the head cut-out used wherever no circle is wanted.
- These files are committed to git on purpose. They are served by Frappe from `eve_agent/public/` at
  `/assets/eve_agent/mascot/animations/<file>` with a 12-hour cache, so **replacing a file in place is invisible
  for up to 12 hours**; new file names appear at once.

## Where the mascot is rendered (current placements)

Component: `frontend/eve_chat/Mascot.vue` (circle crop, reduced-motion handling, the asset base URL constant
`MASCOT_URL`). Used by `frontend/eve_chat/EveChat.vue`:

1. Launcher, bottom-right of every Desk page: a 60 px round button that is the idle clip in a circle
   (`<Mascot />`); on hover or keyboard focus the pill widens and "Ask Eve" slides out beside it.
2. Chat panel header, next to the name "Eve": the 40 px still cut-out, no circle
   (`<Mascot plain :busy="busy" />`); the same header is the minimized bar, so she stays visible while
   minimized. While a reply is being produced (`busy`, lifted from `ChatSession.vue` through a `busy` event)
   the cut-out is replaced by a 40 px circle playing `eve_searching` once, then `eve_searching_looped`; when the reply ends (including stop
   and errors) `eve_searching_reversed` plays once, then the cut-out returns.

Only one of the two is visible at a time (the launcher hides while the panel is open).

All clips an instance can show stay mounted and preloaded; a state change only toggles which one is
visible and playing (a fresh `<video>` per switch showed a blank frame while it loaded). A clip that
stops is rewound while hidden.

`prefers-reduced-motion: reduce` never calls `play()`, so clips show their first frame as a still, and
the outro is skipped (it relies on the `ended` event).

## Adding an expression

1. Drop `<name>.mp4` and `<name>.gif` in this folder.
2. In `Mascot.vue`, the `clip` computed decides which clip plays from the props (`plain`, `busy`); add a
   prop for the new state and a branch that returns the new name. A clip that should play once and hand
   over to another (like the outro) gets its hand-over in `onEnded`.
3. Pass the prop where the state is known. The chat state lives in `ChatSession.vue` (`status`: ready |
   submitted | streaming | error) and reaches the header through the `busy` event; add another event the
   same way for a new state.
4. Update the "current placements" section above.

## Moving to a CDN later

Change `MASCOT_URL` in `Mascot.vue` to the CDN base (it must hold `animations/` and the webp with the same layout) and rebuild. Nothing else references the path.

## After any change to `frontend/`

```bash
cd apps/eve_agent && npm run build     # writes eve_agent/public/dist (committed) and touches hooks.py
```

The widget URL carries the build time, read once when `hooks.py` is imported, so the web process must
restart: the build's `touch` does that for the dev server; production needs `bench restart` and
`bench --site <site> clear-cache`. Then a normal reload (no private window) shows the new build.
