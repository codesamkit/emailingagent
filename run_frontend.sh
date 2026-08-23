#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/frontend"

echo "=========================================================="
echo "⚡ Starting Valence AI Email Agent Web Frontend..."
echo "🎨 Theme: Blue, White, and Black (Autonomous AI Triage)"
echo "=========================================================="

if [ ! -d "node_modules" ]; then
  echo "Installing dependencies..."
  npm install
fi

echo "Launching Vite development server on http://localhost:5173..."
npm run dev
