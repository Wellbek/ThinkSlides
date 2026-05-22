import { NextRequest, NextResponse } from 'next/server';

const AI_URL = process.env.NEXT_PUBLIC_AI_URL || 'http://ai-service:8000';

export async function GET(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${AI_URL}/${path}${request.nextUrl.search}`;
  
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('origin');
  
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers,
      body: null,
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
      { error: 'Failed to reach AI service' },
      { status: 502 }
    );
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${AI_URL}/${path}${request.nextUrl.search}`;
  
  // For file uploads (multipart/form-data)
  const contentType = request.headers.get('content-type') || '';
  let body: any;
  let headers = new Headers(request.headers);
  
  if (contentType.includes('multipart/form-data')) {
    body = await request.formData();
    headers.delete('content-length');
  } else {
    body = await request.text();
  }
  
  headers.delete('host');
  headers.delete('origin');
  
  try {
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
      { error: 'Failed to reach AI service' },
      { status: 502 }
    );
  }
}