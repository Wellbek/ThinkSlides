# ThinkSlides Web Frontend

Next.js 14 App Router frontend with TypeScript and Tailwind CSS.

## Layout

The app has four areas:

1. **Left Sidebar** — Chat history with session search, grouping by date, template badges
2. **Main Chat Area** — Message history, input with file attachments, template selector
3. **Agent Thought Panel** — Real-time SSE event stream rendering
4. **Slide Preview Sidebar** — Live slide thumbnails as they are finalized

## Running Locally

```bash
# Install dependencies
npm install

# Run development server
npm run dev
```

## Pages

- `/` — Main chat interface (AppShell)
- `/templates` — Template management (upload, name, tag, version)
- `/settings` — Organisation settings
- `/auth/login` — Authentication