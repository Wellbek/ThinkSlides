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
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
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

export async function POST(
  request: NextRequest,
  { params }: { params: { slug?: string[] } }
) {
  const path = params.slug ? params.slug.join('/') : '';
  const url = `${ORCHESTRATOR_URL}/${path}${request.nextUrl.search}`;
  
  console.log('Proxying POST:', url);
  
  try {
    const body = await request.text();
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
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
    const response = await fetch(url, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
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