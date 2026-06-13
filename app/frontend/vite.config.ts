import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    // Dedicated port — 5173 is Vite's default and collides with other local
    // projects (e.g. astroviewer). strictPort means we fail loudly rather than
    // silently drifting to 5174+ and breaking CORS.
    port: 5273,
    strictPort: true,
  },
});
