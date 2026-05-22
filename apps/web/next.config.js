/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  transpilePackages: ['@thinkslabs/schemas'],
  experimental: {
    serverComponentsExternalPackages: []
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_AI_URL: process.env.NEXT_PUBLIC_AI_URL,
  },
  serverRuntimeConfig: {
    // Will only be available on the server side
  },
  publicRuntimeConfig: {
    // Will be available on both server and client
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    aiUrl: process.env.NEXT_PUBLIC_AI_URL,
  },
  images: {
    unoptimized: true,
  },
}

module.exports = nextConfig