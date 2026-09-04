Bun + React monitoring UI. Python serves `dist/` on the UI port.

```bash
bun install
bun run dev    # Vite, proxies /api to :8765
bun run build  # writes dist/ for python main.py
```
