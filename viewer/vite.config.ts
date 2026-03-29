import { defineConfig } from "vite";
import path from "path";
import fs from "fs";

const docsDir = path.resolve(__dirname, "../docs");

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/japan-mlit-traffic-data/" : "/",
  publicDir: "public",
  server: {
    port: 5173,
  },
  plugins: [
    {
      name: "serve-docs",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const urlPath = decodeURIComponent(req.url?.split("?")[0] ?? "/");
          const filePath = path.join(docsDir, urlPath);

          if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
            return next();
          }

          const ext = path.extname(filePath).toLowerCase();
          const stat = fs.statSync(filePath);
          const total = stat.size;

          // Content-Type（.gzはoctet-streamでブラウザ自動展開を防ぐ）
          const mime: Record<string, string> = {
            ".json":     "application/json",
            ".gz":       "application/octet-stream",
            ".pmtiles":  "application/octet-stream",
            ".geojson":  "application/json",
          };
          const contentType = mime[ext] ?? "application/octet-stream";

          // Range requestサポート（PMTilesに必要）
          const rangeHeader = req.headers["range"];
          if (rangeHeader) {
            const match = rangeHeader.match(/bytes=(\d+)-(\d*)/);
            if (match) {
              const start = parseInt(match[1], 10);
              const end = match[2] ? parseInt(match[2], 10) : total - 1;
              res.writeHead(206, {
                "Content-Type": contentType,
                "Content-Range": `bytes ${start}-${end}/${total}`,
                "Accept-Ranges": "bytes",
                "Content-Length": String(end - start + 1),
              });
              fs.createReadStream(filePath, { start, end }).pipe(res as NodeJS.WritableStream);
              return;
            }
          }

          res.writeHead(200, {
            "Content-Type": contentType,
            "Content-Length": String(total),
            "Accept-Ranges": "bytes",
          });
          fs.createReadStream(filePath).pipe(res as NodeJS.WritableStream);
        });
      },
    },
  ],
  build: {
    outDir: path.resolve(__dirname, "../docs"),
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl"],
          pmtiles: ["pmtiles"],
        },
      },
    },
  },
}));
