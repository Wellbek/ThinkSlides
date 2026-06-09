/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  transpilePackages: ['@thinkslides/schemas'],
  experimental: {
    serverComponentsExternalPackages: [],
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_AI_URL: process.env.NEXT_PUBLIC_AI_URL,
    NEXT_PUBLIC_DEV_MODE: process.env.NEXT_PUBLIC_DEV_MODE,
  },
  serverRuntimeConfig: {},
  publicRuntimeConfig: {
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    aiUrl: process.env.NEXT_PUBLIC_AI_URL,
    devMode: process.env.NEXT_PUBLIC_DEV_MODE,
  },
  images: {
    unoptimized: true,
  },
};

module.exports = nextConfig;