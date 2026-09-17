import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ChatService } from './chat.service';
import { ChatStreamEvent, ChatThreadMessagesResponse } from './api.models';
import { UserService } from '../auth';

describe('ChatService.getMessages', () => {
  let service: ChatService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ChatService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('requests the thread without query params when no options are given', () => {
    service.getMessages('abc').subscribe();

    const req = httpMock.expectOne('/api/v0/chat/abc');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys()).toEqual([]);
    req.flush({ chat_id: 'abc', messages: [] });
  });

  it('sends limit when provided', () => {
    service.getMessages('abc', { limit: 30 }).subscribe();

    const req = httpMock.expectOne((r) => r.url === '/api/v0/chat/abc');
    expect(req.request.params.get('limit')).toBe('30');
    expect(req.request.params.has('before')).toBe(false);
    req.flush({ chat_id: 'abc', messages: [] });
  });

  it('sends limit and before when paging back', () => {
    service.getMessages('abc', { limit: 30, before: 'msg-1' }).subscribe();

    const req = httpMock.expectOne((r) => r.url === '/api/v0/chat/abc');
    expect(req.request.params.get('limit')).toBe('30');
    expect(req.request.params.get('before')).toBe('msg-1');
    req.flush({ chat_id: 'abc', messages: [] });
  });

  it('encodes the chat id in the path', () => {
    service.getMessages('a/b').subscribe();

    const req = httpMock.expectOne('/api/v0/chat/a%2Fb');
    req.flush({ chat_id: 'a/b', messages: [] });
  });

  it('surfaces the pagination envelope', () => {
    let response: ChatThreadMessagesResponse | undefined;
    service.getMessages('abc', { limit: 2 }).subscribe((res) => (response = res));

    httpMock.expectOne((r) => r.url === '/api/v0/chat/abc').flush({
      chat_id: 'abc',
      messages: [
        { id: 'm1', role: 'human', content: 'Hello' },
        { id: 'm2', role: 'ai', content: 'Hi there!' },
      ],
      next_cursor: 'm1',
    });

    expect(response?.next_cursor).toBe('m1');
    expect(response?.messages.map((m) => m.id)).toEqual(['m1', 'm2']);
  });

  it('reads the last page as one with no cursor', () => {
    let response: ChatThreadMessagesResponse | undefined;
    service.getMessages('abc').subscribe((res) => (response = res));

    httpMock.expectOne((r) => r.url === '/api/v0/chat/abc').flush({
      chat_id: 'abc',
      messages: [{ id: 'm1', role: 'human', content: 'Hello' }],
    });

    expect(response?.next_cursor).toBeUndefined();
  });
});

describe('ChatService.selectVariant', () => {
  let service: ChatService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ChatService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('posts the answer to switch to, with the chat id encoded', () => {
    service.selectVariant('a/b', 'm2', { limit: 30 }).subscribe();

    const req = httpMock.expectOne((r) => r.url === '/api/v0/chat/a%2Fb/variant');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ message_id: 'm2' });
    expect(req.request.params.get('limit')).toBe('30');
    req.flush({ chat_id: 'a/b', messages: [] });
  });

  it('returns the conversation as it reads after the switch', () => {
    let response: ChatThreadMessagesResponse | undefined;
    service.selectVariant('abc', 'm2').subscribe((res) => (response = res));

    httpMock.expectOne((r) => r.url === '/api/v0/chat/abc/variant').flush({
      chat_id: 'abc',
      messages: [
        { id: 'm1', role: 'human', content: 'Hello' },
        {
          id: 'm2',
          role: 'ai',
          content: 'Second try',
          variant_index: 2,
          variant_count: 2,
          prev_variant_id: 'm1b',
        },
      ],
    });

    expect(response?.messages[1].variant_index).toBe(2);
    expect(response?.messages[1].variant_count).toBe(2);
    expect(response?.messages[1].prev_variant_id).toBe('m1b');
  });
});

describe('ChatService SSE event parsing', () => {
  let service: ChatService;
  let originalFetch: typeof globalThis.fetch;

  /**
   * Run a whole SSE body through the real stream and collect what it emits.
   *
   * Driving the public stream rather than the parser keeps the test honest
   * about what a caller actually receives.
   */
  function parseBody(body: string): Promise<ChatStreamEvent[]> {
    globalThis.fetch = (() =>
      Promise.resolve({
        ok: true,
        status: 200,
        body: new ReadableStream<Uint8Array>({
          start: (controller) => {
            controller.enqueue(new TextEncoder().encode(body));
            controller.close();
          },
        }),
      } as unknown as Response)) as typeof globalThis.fetch;

    return new Promise((resolve, reject) => {
      const events: ChatStreamEvent[] = [];
      service.resumeChat('abc', 'hello').subscribe({
        next: (event) => events.push(event),
        error: reject,
        complete: () => resolve(events),
      });
    });
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: UserService,
          useValue: { getValidAccessToken: () => Promise.resolve('test-token') },
        },
      ],
    });
    service = TestBed.inject(ChatService);
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('reads a saved message into its pager position', async () => {
    const events = await parseBody(
      `event: message_saved\ndata: ${JSON.stringify({
        message_id: 'm2',
        human_message_id: 'm1',
        variant_index: 2,
        variant_count: 2,
        prev_variant_id: 'm1b',
      })}\n\n`
    );

    expect(events).toEqual([
      {
        type: 'message_saved',
        messageId: 'm2',
        humanMessageId: 'm1',
        variantIndex: 2,
        variantCount: 2,
        prevVariantId: 'm1b',
      },
    ]);
  });

  it('treats a message with no siblings as the only answer', async () => {
    const events = await parseBody(
      `event: message_saved\ndata: ${JSON.stringify({ message_id: 'm1' })}\n\n`
    );

    expect(events[0]).toMatchObject({ type: 'message_saved', variantIndex: 1, variantCount: 1 });
  });

  // The token branch is what writes an answer's text, so an unhandled named
  // event would be spliced into the answer as raw JSON.
  it('never mistakes a named event for a token', async () => {
    const events = await parseBody(
      'event: message_saved\ndata: {"message_id":"m1"}\n\n' +
        'event: title\ndata: {"title":"A thread"}\n\n' +
        'event: error\ndata: {"error":"boom"}\n\n'
    );

    expect(events.map((event) => event.type)).toEqual(['message_saved', 'title', 'error']);
  });

  it('reads an unnamed event as a token', async () => {
    expect(await parseBody('data: "Hello"\n\n')).toEqual([{ type: 'token', content: 'Hello' }]);
  });
});
