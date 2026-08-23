# Valence — AI Email Agent Frontend

Autonomous AI Email Agent web interface styled in a modern, high-contrast **Blue, White, and Black** design system.

---

## 🎨 Theme & Aesthetic System
- **Dark Obsidian Bases**: `#070B14`, `#0D1424`, `#131C31` with glassmorphic backdrop blur.
- **Electric & Sapphire Blues**: `#2563EB`, `#3B82F6`, `#60A5FA` glowing accents, score rings, and action buttons.
- **Crisp Porcelain Whites**: High contrast typography and readability.

---

## 🚀 Key Features

1. **Intelligent Email Inbox Triage**:
   - Live AI importance score radial meters (0–100) with rule-based justifications.
   - 1–3 sentence AI factual summaries highlighting key topics, asks, and deadlines.
   - Automated **No-Reply** shields preventing accidental outline generation for transactional messages.
   - **Google Calendar** integration with automatic free/busy checking and open slot recommendations.

2. **AI Reply Outline Engine**:
   - Interactive bullet-point outline editor (add, edit, delete, reorder).
   - Code-level safety gating: outlines only generate when emails are marked **Read** and are **not No-Reply**.
   - One-click **"Expand to Full Draft"** modal with tone customization (Professional, Concise, Direct, Warm) and clipboard/email export.
   - **Stale Thread** detection warnings when newer messages arrive in an active thread.

3. **Multi-View Suite**:
   - **Inbox Triage**: Master-detail view with multi-criteria filters (Read/Unread, Urgent/High/Medium/Low, Scheduling, Outlines, Stale, No-Reply).
   - **Smart Calendar**: Weekly schedule grid with conflict detection and recommended meeting slots.
   - **Agent Insights & Analytics**: Real-time triage accuracy, processing latency, and volume distribution.
   - **Agent Rules & VIPs**: Manage VIP senders (+25 pt boost), inspect OAuth scopes, and tune AI policies.

4. **Dual Execution Mode**:
   - **Live Backend Mode**: Connects directly to FastAPI endpoints (`/api/emails`, `/api/stats`, `/api/emails/{id}/outline`, etc.).
   - **Standalone Demo Mode**: Fully functional offline fallback with rich test fixtures covering all edge cases.

---

## 🛠️ Quick Start

```bash
# Option 1: Using the helper script from project root
./run_frontend.sh

# Option 2: Using npm inside frontend directory
cd frontend
npm install
npm run dev
```

Visit **`http://localhost:5173`** in your browser.

---

## ⌨️ Keyboard Shortcuts
- `j` or `↓`: Move selection to next email
- `k` or `↑`: Move selection to previous email
- `r`: Toggle read / unread status on selected email
- `/`: Focus universal search bar
