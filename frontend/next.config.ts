import type { NextConfig } from "next";

const apiUrl = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  skipTrailingSlashRedirect: true,
  turbopack: {
    rules: {
      "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" },
    },
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiUrl}/:path*` },
      { source: "/pitch/", destination: "/pitch/index.html" },
    ];
  },
};

export default nextConfig;
