# Copilot Low-Cost Workflow for Vinyl

This repo is easiest to work on when prompts stay close to one file and one function.

## Cost Ranking

1. `charts.py` and `utils.py` are the cheapest surfaces. Use these for chart, layout, and presentation changes.
2. `app.py` is the most expensive surface. It contains login, sidebar actions, tabs, caching, and most UI state.
3. `discogs_api.py` is the next most expensive surface. It bundles the rate-limited Discogs sync and fetch flows.
4. `database.py` is expensive when prompts touch schema or shared data access.

## Lowest-Cost Workflow

1. Start with the smallest callable boundary you can name.
2. Search for the exact function or call site before asking for a change.
3. Keep one request to one module and one behavior.
4. Validate only the touched path first.
5. Split mixed UI/API changes into two passes.

## Good Prompt Shapes

Use prompts like these instead of broad requests:

- "In `charts.py`, adjust `_plotly_bar` so the x-axis labels wrap better on mobile."
- "In `utils.py`, change `_pressing_badge` to show only notable pressing descriptors."
- "In `app.py`, in the Browse tab list view only, change the Log button label and tooltip."
- "In `discogs_api.py`, update `sync_wantlist` to skip items missing `release_id`."

## Expensive Prompt Shapes

Avoid requests like these:

- "Improve the whole app."
- "Refactor the sync pipeline."
- "Make the UI better everywhere."
- "Optimize the database layer."

## Reusable Template

```
Change only <function or block> in <file>.
Goal: <single behavior change>.
Constraints: do not touch unrelated modules; keep existing behavior outside the target slice.
Validate with: <single narrow check or syntax compile>.
```

## Repo-Specific Notes

- `app.py` is wired around `@st.cache_data(ttl=300)` and the `_load_collection_data.clear()` refresh pattern, so prefer minimal edits around those boundaries.
- `discogs_api.py` has `_RATE_SLEEP = 1.1`, so changes there should be narrow and deliberate.
- `refresh_all.py` is a good reference for the existing sync flow without reopening the whole UI.
