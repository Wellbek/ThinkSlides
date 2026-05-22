/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  transpilePackages: ['@slideagent/schemas'],
  experimental: {
    serverComponentsExternalPackages: [],
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_AI_URL: process.env.NEXT_PUBLIC_AI_URL,
  },
  serverRuntimeConfig: {},
  publicRuntimeConfig: {
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    aiUrl: process.env.NEXT_PUBLIC_AI_URL,
  },
  images: {
    unoptimized: true,
  },
};

module.exports = nextConfig;