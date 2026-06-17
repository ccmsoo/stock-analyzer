/** @type {import('next').NextConfig} */
const nextConfig = {
  // ESLint(스타일)은 배포를 막지 않게 — 타입체크(tsc)는 그대로 유지됨
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
