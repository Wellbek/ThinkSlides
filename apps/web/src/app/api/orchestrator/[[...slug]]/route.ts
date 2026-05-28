import { NextRequest, NextResponse } from 'next/server';

const ORCHESTRATOR_URL = process.env.NEXT_PUBLIC_API_URL || 'http://orchestrator:8080';

export async function GET(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${ORCHESTRATOR_URL}/${path}${request.nextUrl.search}`;
  
  console.log('Proxying GET:', url);

  try {
    // Forward necessary headers
    const headers: HeadersInit = {};
    const authHeader = request.headers.get('Authorization');
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'GET',
      headers,
    });

    const contentType = response.headers.get('Content-Type');

    // For SSE (text/event-stream), stream the response
    if (contentType === 'text/event-stream') {
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Response body is null');
      }

      const encoder = new TextEncoder();
      const decoder = new TextDecoder();

      // Create a ReadableStream that forwards chunks as they arrive
      const stream = new ReadableStream({
        async start(controller) {
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              const chunk = decoder.decode(value, { stream: true });
              controller.enqueue(encoder.encode(chunk));
            }
            controller.close();
          } catch (err) {
            console.error('Stream error:', err);
            controller.error(err);
          }
        },
      });

      return new Response(stream, {
        status: response.status,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          'X-Accel-Buffering': 'no', // Disable nginx/proxy buffering
        },
      });
    } else {
      // For non-SSE responses (JSON, etc.), read all and return
      const data = await response.text();
      return new NextResponse(data, {
        status: response.status,
        headers: {
          'Content-Type': contentType || 'application/json',
        },
      });
    }
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${ORCHESTRATOR_URL}/${path}${request.nextUrl.search}`;
  
  console.log('Proxying POST:', url);

  try {
    const body = await request.text();
    const authHeader = request.headers.get('Authorization');

    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body,
    });

    const data = await response.text();
    return new NextResponse(data, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${ORCHESTRATOR_URL}/${path}${request.nextUrl.search}`;
  
  console.log('Proxying DELETE:', url);

  try {
    const authHeader = request.headers.get('Authorization');
    const headers: HeadersInit = {};
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const response = await fetch(url, {
      method: 'DELETE',
      headers,
    });

    const data = await response.text();
    return new NextResponse(data, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return NextResponse.json(
      { error: 'Failed to reach orchestrator service' },
      { status: 502 }
    );
  }
}