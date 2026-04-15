import type { NextConfig } from "next";

const pythonApiUrl = process.env["PYTHON_API_URL"];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  ...(pythonApiUrl !== undefined
    ? {
        rewrites(): Promise<{ source: string; destination: string }[]> {
          return Promise.resolve([
            { source: "/api/:path*", destination: `${pythonApiUrl}/api/:path*` },
          ]);
        },
      }
    : {}),
};

export default nextConfig;
