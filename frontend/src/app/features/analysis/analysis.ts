import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  Injector,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { DatePipe, Location } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { map, Subscription, switchMap } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import {
  ChatMessage,
  ChatMessageResponse,
  ChatMessageSegment,
  ChatRole,
  ChatService,
  ChatStreamEvent,
  ChatThread,
  ChatThreadMessagesResponse,
  DeepAnalysis,
  DeepAnalysisDetail,
  DeepAnalysisService,
  RepoStore,
} from '../../core/api';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { MarkdownPipe, MarkdownStreamPipe } from '../../shared/markdown.pipe';
import { MermaidDirective } from '../../shared/mermaid.directive';
import { timeAgo } from '../../shared/time-ago';
import { GraphExplorer } from './graph-explorer/graph-explorer';
import { AnalysisActionService } from './analysis-action.service';
import { DeepAnalysisDialog } from './deep-analysis-dialog/deep-analysis-dialog';
import { DeepAnalysisDetailComponent } from './deep-analysis-detail/deep-analysis-detail';
import { UiButton, UiSpinner } from '@design-system';

@Component({
  selector: 'app-analysis',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphExplorer, DeepAnalysisDialog, DeepAnalysisDetailComponent, FormsModule, DatePipe, MarkdownPipe, MarkdownStreamPipe, MermaidDirective, TranslateModule, UiButton, UiSpinner],
  templateUrl: './analysis.html',
  styleUrl: './analysis.scss',
})
export class Analysis implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly location = inject(Location);
  private readonly repoStore = inject(RepoStore);
  private readonly chatService = inject(ChatService);
  private readonly deepAnalysisService = inject(DeepAnalysisService);
  private readonly breadcrumbService = inject(BreadcrumbService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly translateService = inject(TranslateService);
  private readonly analysisActionService = inject(AnalysisActionService);

  /** Number of messages fetched per history page. */
  private static readonly PAGE_SIZE = 30;

  protected readonly repoId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('id')!))
  );

  protected readonly repo = toSignal(
    this.route.paramMap.pipe(
      switchMap((params) => this.repoStore.getRepo(params.get('id')!))
    )
  );

  protected readonly files = toSignal(
    this.route.paramMap.pipe(
      switchMap((params) => this.repoStore.getRepoFiles(params.get('id')!))
    ),
    { initialValue: [] }
  );

  protected readonly fileCount = computed(() => this.files().length);
  protected readonly repoName = computed(() => this.repo()?.name ?? '');

  // A zip upload is the repository with no commit pinned to it — the same test
  // the backend uses to tell its two sources apart
  protected readonly isZipUpload = computed(() => !this.repo()?.repo_hash);

  protected readonly branchCount = signal(12);
  protected readonly contributorCount = signal(8);

  protected readonly activeRole = signal<'Developer' | 'Business'>('Developer');

  // Thread history
  protected readonly threads = signal<ChatThread[]>([]);
  protected readonly activeChatId = signal<string | null>(null);
  protected readonly loadingThreads = signal(false);
  protected readonly loadingMessages = signal(false);

  protected readonly suggestions = computed(() => [
    this.translateService.instant('analysis.suggestion1'),
    this.translateService.instant('analysis.suggestion2'),
    this.translateService.instant('analysis.suggestion3'),
    this.translateService.instant('analysis.suggestion4'),
    this.translateService.instant('analysis.suggestion5'),
    this.translateService.instant('analysis.suggestion6'),
  ]);

  protected readonly isGraphOpen = signal(false);
  protected readonly isToolsMenuOpen = signal(false);
  private previousFocus: HTMLElement | null = null;
  private graphOpenedFromChat = false;

  // Chat state
  protected readonly isChatOpen = signal(false);
  protected readonly chatMessages = signal<ChatMessage[]>([]);
  protected readonly chatContext = signal<{ fileName: string; nodeLabel: string } | null>(null);
  protected readonly chatInputValue = signal('');
  protected readonly customQuestionValue = signal('');
  protected readonly isStreaming = signal(false);
  /** Set while an existing answer is being replaced, rather than a new one appended. */
  protected readonly isRetrying = signal(false);
  /**
   * Set when a regeneration produced nothing and the previous answer was put back.
   *
   * Needed because the failure leaves no trace in the transcript: unlike a
   * failed send, there is no empty bubble to write the error into.
   */
  protected readonly retryFailed = signal(false);
  /** The answer currently being switched to, so its pager can be disabled. */
  protected readonly switchingVariantId = signal<string | null>(null);
  protected readonly isSwitchingVariant = computed(() => this.switchingVariantId() !== null);
  protected readonly activeTools = signal<Map<string, string>>(new Map());
  protected readonly hasActiveTool = computed(() => this.activeTools().size > 0);
  protected readonly streamSegments = signal<ChatMessageSegment[]>([]);
  protected readonly expandedReasoning = signal<Set<string>>(new Set());

  // Lazy history loading
  protected readonly loadingOlderMessages = signal(false);
  private readonly messagesCursor = signal<string | null>(null);
  /** The server sends a cursor only while older messages remain. */
  protected readonly hasMoreMessages = computed(() => this.messagesCursor() !== null);
  private readonly messagesContainer =
    viewChild<ElementRef<HTMLElement>>('messagesContainer');
  private readonly topSentinel = viewChild<ElementRef<HTMLElement>>('topSentinel');
  private topObserver: IntersectionObserver | null = null;
  private awaitingInitialScroll = false;
  private localKeySeq = 0;
  /**
   * Bumped every time the list is replaced wholesale.
   *
   * Switching answers swaps the conversation for a different branch, so an
   * older page requested before the switch describes messages that are no
   * longer on screen; prepending it would mix the two branches together.
   */
  private historyGeneration = 0;

  private scrollScheduled = false;

  protected readonly chatSuggestions = computed(() => [
    this.translateService.instant('analysis.financialCommitment'),
    this.translateService.instant('analysis.specificInitiatives'),
    this.translateService.instant('analysis.identifyActors'),
  ]);

  // Deep analysis state
  protected readonly activeHistoryTab = signal<'chat' | 'deepAnalysis'>('chat');
  protected readonly deepAnalyses = signal<DeepAnalysis[]>([]);
  protected readonly activeDeepAnalysisId = signal<string | null>(null);
  protected readonly activeDeepAnalysisDetail = signal<DeepAnalysisDetail | null>(null);
  protected readonly isDeepAnalysisDialogOpen = signal(false);
  protected readonly isDeepAnalysisViewOpen = computed(() => this.activeDeepAnalysisId() !== null);
  private deepAnalysisPollSub: Subscription | null = null;

  private streamSub: Subscription | null = null;

  protected readonly chatContextLabel = computed(() => {
    const ctx = this.chatContext();
    if (!ctx) return this.translateService.instant('analysis.askPlaceholder');
    return this.translateService.instant('analysis.contextPlaceholder', {
      fileName: ctx.fileName,
      nodeLabel: ctx.nodeLabel,
    });
  });

  constructor() {
    effect(() => {
      const name = this.repoName();
      const id = this.repoId();
      if (name && id) {
        this.breadcrumbService.set([
          { label: 'common.dashboard', route: '/' },
          { label: name, route: '/project/' + id },
          { label: 'common.analysis' },
        ]);
      } else {
        this.breadcrumbService.set([
          { label: 'common.dashboard', route: '/' },
          { label: 'common.loading', route: '/' },
          { label: 'common.analysis' },
        ]);
      }
    });

    effect(() => {
      const id = this.repoId();
      if (id) {
        this.refreshThreads();
        this.refreshDeepAnalyses();
      }
    });

    // The message list and its sentinel live inside `@if` branches, so both
    // refs come and go as threads are switched. Re-attach the observer each
    // time they change.
    effect(() => {
      const container = this.messagesContainer()?.nativeElement;
      const sentinel = this.topSentinel()?.nativeElement;

      this.topObserver?.disconnect();
      this.topObserver = null;

      if (!container || !sentinel) return;

      this.topObserver = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) {
            this.loadOlderMessages();
          }
        },
        { root: container, rootMargin: '200px 0px 0px 0px' }
      );
      this.topObserver.observe(sentinel);
    });

    this.destroyRef.onDestroy(() => this.topObserver?.disconnect());
  }

  ngOnInit(): void {
    this.analysisActionService.restart$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.startNewAnalysis());

    // Restore state from URL on init (supports page refresh)
    const params = this.route.snapshot.paramMap;
    const chatId = params.get('chatId');
    const analysisId = params.get('analysisId');

    if (chatId) {
      this.selectThreadById(chatId);
    } else if (analysisId) {
      this.selectDeepAnalysisById(analysisId);
    }
  }

  protected setRole(role: 'Developer' | 'Business'): void {
    this.activeRole.set(role);
  }

  protected openGraph(): void {
    this.previousFocus = document.activeElement as HTMLElement;
    this.isGraphOpen.set(true);
  }

  protected closeGraph(): void {
    this.isGraphOpen.set(false);
    this.graphOpenedFromChat = false;
    setTimeout(() => this.previousFocus?.focus());
  }

  protected toggleToolsMenu(): void {
    this.isToolsMenuOpen.update((v) => !v);
  }

  protected closeToolsMenu(): void {
    this.isToolsMenuOpen.set(false);
  }

  protected openGraphFromToolsMenu(): void {
    this.isToolsMenuOpen.set(false);
    this.graphOpenedFromChat = true;
    this.openGraph();
  }

  protected clearChatContext(): void {
    this.chatContext.set(null);
  }

  protected startNewAnalysis(): void {
    this.streamSub?.unsubscribe();
    this.activeChatId.set(null);
    this.activeDeepAnalysisId.set(null);
    this.activeDeepAnalysisDetail.set(null);
    this.deepAnalysisPollSub?.unsubscribe();
    this.isChatOpen.set(false);
    this.chatMessages.set([]);
    this.chatContext.set(null);
    this.chatInputValue.set('');
    this.isStreaming.set(false);
    this.isRetrying.set(false);
    this.retryFailed.set(false);
    this.switchingVariantId.set(null);
    this.activeTools.set(new Map());
    this.streamSegments.set([]);
    this.expandedReasoning.set(new Set());
    this.loadingOlderMessages.set(false);
    this.messagesCursor.set(null);
    this.awaitingInitialScroll = false;
    this.loadingMessages.set(false);
    this.updateUrl();
  }

  protected onChatRequested(event: { nodeLabel: string; fileName: string }): void {
    this.isGraphOpen.set(false);
    this.chatContext.set(event);

    if (this.graphOpenedFromChat) {
      this.graphOpenedFromChat = false;
      return;
    }

    this.activeChatId.set(null);
    this.isChatOpen.set(true);
    this.chatMessages.set([
      {
        key: this.nextLocalKey(),
        role: 'assistant',
        content: this.translateService.instant('analysis.chatWelcome'),
        timestamp: new Date(),
      },
    ]);
  }

  protected selectThread(thread: ChatThread): void {
    this.selectThreadById(thread.chat_id);
  }

  private selectThreadById(chatId: string): void {
    this.streamSub?.unsubscribe();
    this.activeDeepAnalysisId.set(null);
    this.activeDeepAnalysisDetail.set(null);
    this.deepAnalysisPollSub?.unsubscribe();
    this.activeChatId.set(chatId);
    this.chatMessages.set([]);
    this.isChatOpen.set(true);
    this.loadingMessages.set(true);
    this.isStreaming.set(false);
    this.isRetrying.set(false);
    this.retryFailed.set(false);
    this.switchingVariantId.set(null);
    this.activeTools.set(new Map());
    this.streamSegments.set([]);
    this.expandedReasoning.set(new Set());
    this.loadingOlderMessages.set(false);
    this.messagesCursor.set(null);
    this.awaitingInitialScroll = true;
    this.activeHistoryTab.set('chat');
    this.updateUrl();

    this.chatService
      .getMessages(chatId, { limit: Analysis.PAGE_SIZE })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.applyMessagePage(res);
          this.loadingMessages.set(false);
        },
        error: () => {
          this.awaitingInitialScroll = false;
          this.chatMessages.set([
            {
              key: this.nextLocalKey(),
              role: 'assistant',
              content: this.translateService.instant('analysis.chatError'),
            },
          ]);
          this.messagesCursor.set(null);
          this.loadingMessages.set(false);
        },
      });
  }

  /**
   * Render a freshly fetched page as the entire conversation.
   *
   * Used when opening a thread and when switching branches: in both cases
   * whatever was on screen no longer describes the conversation, so the page
   * replaces the list rather than merging into it.
   */
  private applyMessagePage(res: ChatThreadMessagesResponse): void {
    this.historyGeneration++;
    this.chatMessages.set(res.messages.map((m) => this.toChatMessage(m)));
    // `hasMoreMessages` derives from the cursor, so setting it is enough.
    this.messagesCursor.set(res.next_cursor ?? null);
    this.streamSegments.set([]);
    this.retryFailed.set(false);
    this.loadingOlderMessages.set(false);
    this.awaitingInitialScroll = true;
    // Only the newest page is loaded, so the conversation must open at its
    // end — otherwise the top sentinel is immediately in view and would
    // cascade-load the whole history.
    this.afterRender(() => {
      this.scrollToBottom();
      this.awaitingInitialScroll = false;
      this.fillViewportIfNeeded();
    });
  }

  /** Load the page of messages preceding the ones currently displayed. */
  private loadOlderMessages(): void {
    const chatId = this.activeChatId();
    const before = this.messagesCursor();
    if (
      !chatId ||
      // No cursor means the history is exhausted.
      !before ||
      this.loadingOlderMessages() ||
      // The sentinel starts in view until the list is scrolled to its end;
      // ignore it until that has happened.
      this.awaitingInitialScroll
    ) {
      return;
    }

    this.loadingOlderMessages.set(true);
    const generation = this.historyGeneration;

    this.chatService
      .getMessages(chatId, { limit: Analysis.PAGE_SIZE, before })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          // The conversation was replaced while this page was in flight, so
          // it belongs to a branch the user is no longer reading.
          if (generation !== this.historyGeneration) return;

          // Measure immediately before the prepend: the list may have grown at
          // the bottom while the request was in flight (e.g. a live stream).
          const container = this.messagesContainer()?.nativeElement;
          const prevScrollHeight = container?.scrollHeight ?? 0;
          const prevScrollTop = container?.scrollTop ?? 0;

          const older = res.messages.map((m) => this.toChatMessage(m));
          this.chatMessages.update((list) => [...older, ...list]);
          this.messagesCursor.set(res.next_cursor ?? null);
          this.loadingOlderMessages.set(false);
          // Keep the message the user was reading in place as content grows above it.
          this.afterRender(() => {
            const el = this.messagesContainer()?.nativeElement;
            if (!el) return;
            el.scrollTop = el.scrollHeight - prevScrollHeight + prevScrollTop;
            this.fillViewportIfNeeded();
          });
        },
        error: () => {
          if (generation !== this.historyGeneration) return;
          this.loadingOlderMessages.set(false);
        },
      });
  }

  /** Map a server message onto the render model, keyed by its document ID. */
  private toChatMessage(message: ChatMessageResponse): ChatMessage {
    const role = this.normalizeRole(message.role);
    return {
      key: message.id,
      id: message.id,
      role,
      ...this.parseMessageContext(message.content, role),
      variantIndex: message.variant_index,
      variantCount: message.variant_count,
      prevVariantId: message.prev_variant_id,
      nextVariantId: message.next_variant_id,
    };
  }

  /** A key for a message that exists only on the client (optimistic or local). */
  private nextLocalKey(): string {
    return `local-${++this.localKeySeq}`;
  }

  private afterRender(fn: () => void): void {
    afterNextRender(fn, { injector: this.injector });
  }

  private scrollToBottom(): void {
    const el = this.messagesContainer()?.nativeElement;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }

  /**
   * Scroll to the end after the next render, at most once per frame.
   *
   * Streamed tokens arrive far faster than the browser paints, so scheduling
   * one callback per token would queue hundreds of redundant scrolls.
   */
  private scheduleScrollToBottom(): void {
    if (this.scrollScheduled) return;

    this.scrollScheduled = true;
    this.afterRender(() => {
      this.scrollScheduled = false;
      this.scrollToBottom();
    });
  }

  /**
   * Pull another page when the loaded ones do not overflow the container.
   *
   * The sentinel stays on screen in that case, and `IntersectionObserver`
   * only reports transitions — so no further callback would ever arrive and
   * the user would be stuck with no way to reach the older messages.
   */
  private fillViewportIfNeeded(): void {
    const el = this.messagesContainer()?.nativeElement;
    if (el && el.scrollHeight <= el.clientHeight) {
      this.loadOlderMessages();
    }
  }

  protected deleteThread(event: Event, chatId: string): void {
    event.stopPropagation();
    this.chatService
      .deleteThread(chatId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.threads.update((list) => list.filter((t) => t.chat_id !== chatId));
        if (this.activeChatId() === chatId) {
          this.startNewAnalysis();
        }
      });
  }

  protected threadTimeAgo(isoDate: string): string {
    return timeAgo(isoDate, this.translateService);
  }

  protected copyMessage(msg: ChatMessage): void {
    navigator.clipboard.writeText(msg.content);
  }

  protected sendMessage(): void {
    const text = this.chatInputValue().trim();
    if (!text || this.isStreaming()) return;

    this.retryFailed.set(false);

    const ctx = this.chatContext();
    const fullMessage = ctx ? `[Context: ${ctx.fileName} > ${ctx.nodeLabel}] ${text}` : text;

    const userKey = this.nextLocalKey();
    this.chatMessages.update((msgs) => [
      ...msgs,
      {
        key: userKey,
        role: 'user',
        content: text,
        timestamp: new Date(),
        context: ctx ?? undefined,
      },
    ]);
    this.chatInputValue.set('');
    this.isStreaming.set(true);
    this.streamSegments.set([]);

    // Target the streamed message by key: loading older pages prepends to the
    // list, so a captured array index would drift onto the wrong message.
    const assistantKey = this.nextLocalKey();
    const assistantMsg: ChatMessage = {
      key: assistantKey,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    };
    this.chatMessages.update((msgs) => [...msgs, assistantMsg]);

    this.scheduleScrollToBottom();

    this.streamSub?.unsubscribe();

    const chatId = this.activeChatId();
    const stream$ = chatId
      ? this.chatService.resumeChat(chatId, fullMessage)
      : this.chatService.createChat(this.repoId()!, fullMessage);

    // Set by an `error` event, so the stream's completion leaves the reported
    // failure on screen instead of rebuilding the bubble from the segments.
    let failed = false;

    this.streamSub = stream$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (event) => {
        // Every event type below grows the list or the streaming panel.
        this.scheduleScrollToBottom();

        if (event.type === 'chat_id') {
          this.activeChatId.set(event.chatId);
          this.updateUrl();
        } else if (event.type === 'error') {
          failed = true;
          this.showStreamError(assistantKey, event.detail);
        } else {
          this.applyStreamEvent(event, assistantKey, userKey);
        }
      },
      error: () => {
        this.showStreamError(assistantKey);
        this.isStreaming.set(false);
        this.activeTools.set(new Map());
        this.scheduleScrollToBottom();
      },
      complete: () => {
        this.isStreaming.set(false);
        this.activeTools.set(new Map());
        if (!failed) {
          this.finalizeStreamedMessage(assistantKey);
        }
        this.refreshThreads();
        // Ending the stream swaps the live panel for the final bubble, which
        // changes the list's height.
        this.scheduleScrollToBottom();
      },
    });
  }

  /**
   * Answer the last question again, keeping the answer it already has.
   *
   * The new answer replaces the existing bubble instead of being appended:
   * the two are alternatives to the same question, and the pager is how the
   * user moves between them.
   */
  protected retry(msg: ChatMessage): void {
    const chatId = this.activeChatId();
    const messageId = msg.id;
    if (!chatId || !messageId || this.isStreaming() || this.isRetrying()) return;

    const previous = msg;
    const assistantKey = msg.key;

    // Cleared before the live panel is switched on, or the previous turn's
    // segments would flash in place of the answer being regenerated.
    this.streamSegments.set([]);
    this.activeTools.set(new Map());
    this.retryFailed.set(false);
    this.isRetrying.set(true);
    this.isStreaming.set(true);
    this.patchMessage(assistantKey, { content: '', reasoning: undefined });
    this.scheduleScrollToBottom();

    this.streamSub?.unsubscribe();

    let failed = false;
    let saved = false;

    this.streamSub = this.chatService
      .retryMessage(chatId, messageId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (event) => {
          this.scheduleScrollToBottom();

          if (event.type === 'message_saved') {
            saved = true;
            this.applyStreamEvent(event, assistantKey);
          } else if (event.type === 'error') {
            failed = true;
            this.restoreMessage(previous);
          } else {
            this.applyStreamEvent(event, assistantKey);
          }
        },
        error: () => {
          // Once the answer is saved the server has already switched to it,
          // so restoring the old one here would only disagree with it.
          if (saved) {
            this.reloadActivePage();
          } else {
            this.restoreMessage(previous);
          }
          this.endRetry();
        },
        complete: () => {
          if (!failed) {
            this.finalizeStreamedMessage(assistantKey);
          }
          this.endRetry();
          this.refreshThreads();
        },
      });
  }

  /**
   * Switch the conversation onto another answer to the same question.
   *
   * The turns that followed the other answer are hidden and the ones that
   * followed this one return, so the whole conversation from that point on
   * is replaced by what the server sends back.
   */
  protected switchVariant(messageId: string | undefined): void {
    const chatId = this.activeChatId();
    if (!chatId || !messageId || this.isStreaming() || this.isSwitchingVariant()) return;

    this.switchingVariantId.set(messageId);

    this.chatService
      .selectVariant(chatId, messageId, { limit: Analysis.PAGE_SIZE })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.applyMessagePage(res);
          this.switchingVariantId.set(null);
        },
        error: () => {
          this.switchingVariantId.set(null);
        },
      });
  }

  /**
   * Apply one streamed event to the answer being written.
   *
   * Shared by sending and regenerating: both write into a single assistant
   * bubble, addressed by key because loading older pages prepends to the
   * list and would shift any captured index.
   */
  private applyStreamEvent(
    event: ChatStreamEvent,
    assistantKey: string,
    userKey?: string
  ): void {
    if (event.type === 'tool_start') {
      this.activeTools.update((m) => {
        const next = new Map(m);
        next.set(event.id, event.tool);
        return next;
      });
      this.streamSegments.update((segs) => [
        ...segs,
        { type: 'tool', content: event.tool, toolId: event.id },
      ]);
    } else if (event.type === 'tool_end') {
      this.activeTools.update((m) => {
        const next = new Map(m);
        next.delete(event.id);
        return next;
      });
      this.streamSegments.update((segs) => {
        const updated = segs.map((seg) =>
          seg.type === 'tool' && seg.toolId === event.id
            ? { ...seg, toolStatus: event.status }
            : seg
        );
        return [...updated, { type: 'text' as const, content: '' }];
      });
    } else if (event.type === 'message_saved') {
      this.patchMessage(assistantKey, {
        id: event.messageId,
        variantIndex: event.variantIndex,
        variantCount: event.variantCount,
        prevVariantId: event.prevVariantId,
        // The freshly generated answer is always the newest of its group.
        nextVariantId: undefined,
      });
      if (userKey && event.humanMessageId) {
        this.patchMessage(userKey, { id: event.humanMessageId });
      }
    } else if (event.type === 'token') {
      this.streamSegments.update((segs) => {
        const last = segs[segs.length - 1];
        if (last && last.type === 'text') {
          const updated = [...segs];
          updated[updated.length - 1] = { ...last, content: last.content + event.content };
          return updated;
        }
        return [...segs, { type: 'text', content: event.content }];
      });
      this.chatMessages.update((msgs) =>
        msgs.map((msg) =>
          msg.key === assistantKey ? { ...msg, content: msg.content + event.content } : msg
        )
      );
    } else if (event.type === 'title') {
      // Applied straight away so the sidebar renames as the answer is being
      // written, rather than waiting for the thread list to be refetched.
      this.applyThreadTitle(event.title);
    }
  }

  /** Re-read the newest page, when the client can no longer trust its own copy. */
  private reloadActivePage(): void {
    const chatId = this.activeChatId();
    if (!chatId) return;
    this.chatService
      .getMessages(chatId, { limit: Analysis.PAGE_SIZE })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => this.applyMessagePage(res),
      });
  }

  /** Merge changes into one message, leaving its `key` and the rest untouched. */
  private patchMessage(key: string, patch: Partial<ChatMessage>): void {
    this.chatMessages.update((msgs) =>
      msgs.map((msg) => (msg.key === key ? { ...msg, ...patch } : msg))
    );
  }

  /** Put a message back as it was, after a regeneration that produced nothing. */
  private restoreMessage(previous: ChatMessage): void {
    this.chatMessages.update((msgs) =>
      msgs.map((msg) => (msg.key === previous.key ? previous : msg))
    );
    this.streamSegments.set([]);
    this.retryFailed.set(true);
  }

  /**
   * Report a failed turn in the bubble it was being written into.
   *
   * The server's own wording wins when it sent one, because it says what went
   * wrong; a partial answer is kept otherwise, and the generic message is the
   * last resort for a stream that died without explaining itself.
   */
  private showStreamError(assistantKey: string, detail?: string): void {
    this.chatMessages.update((msgs) =>
      msgs.map((msg) =>
        msg.key === assistantKey
          ? {
              ...msg,
              content:
                detail || msg.content || this.translateService.instant('analysis.chatError'),
            }
          : msg
      )
    );
  }

  private endRetry(): void {
    this.isRetrying.set(false);
    this.isStreaming.set(false);
    this.activeTools.set(new Map());
    this.scheduleScrollToBottom();
  }

  protected onCustomQuestionSubmit(): void {
    const text = this.customQuestionValue().trim();
    if (!text) return;
    this.customQuestionValue.set('');
    this.onDefaultSuggestionClick(text);
  }

  protected onDefaultSuggestionClick(question: string): void {
    this.activeChatId.set(null);
    this.isChatOpen.set(true);
    this.chatMessages.set([
      {
        key: this.nextLocalKey(),
        role: 'assistant',
        content: this.translateService.instant('analysis.chatWelcome'),
        timestamp: new Date(),
      },
    ]);
    this.chatInputValue.set(question);
    this.sendMessage();
  }

  protected onSuggestionClick(question: string): void {
    this.chatInputValue.set(question);
    this.sendMessage();
  }

  protected isUserRole(role: ChatRole): boolean {
    return role === 'user' || role === 'human';
  }

  protected isAssistantRole(role: ChatRole): boolean {
    return role === 'assistant' || role === 'ai';
  }

  protected toggleReasoning(key: string): void {
    this.expandedReasoning.update((set) => {
      const next = new Set(set);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  protected isReasoningExpanded(key: string): boolean {
    return this.expandedReasoning().has(key);
  }

  // Deep analysis methods
  protected setHistoryTab(tab: 'chat' | 'deepAnalysis'): void {
    this.activeHistoryTab.set(tab);
  }

  protected openDeepAnalysisDialog(): void {
    this.isDeepAnalysisDialogOpen.set(true);
  }

  protected closeDeepAnalysisDialog(): void {
    this.isDeepAnalysisDialogOpen.set(false);
  }

  protected onDeepAnalysisStarted(analysisId: string): void {
    this.isDeepAnalysisDialogOpen.set(false);
    this.activeHistoryTab.set('deepAnalysis');
    this.refreshDeepAnalyses();
    this.selectDeepAnalysisById(analysisId);
  }

  protected selectDeepAnalysis(analysis: DeepAnalysis): void {
    this.selectDeepAnalysisById(analysis.id);
  }

  protected deleteDeepAnalysis(event: Event, analysisId: string): void {
    event.stopPropagation();
    this.deepAnalysisService
      .deleteAnalysis(analysisId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.deepAnalyses.update((list) => list.filter((a) => a.id !== analysisId));
        if (this.activeDeepAnalysisId() === analysisId) {
          this.activeDeepAnalysisId.set(null);
          this.activeDeepAnalysisDetail.set(null);
          this.deepAnalysisPollSub?.unsubscribe();
        }
      });
  }

  protected onDownloadPdf(analysisId: string): void {
    this.deepAnalysisService.downloadPdf(analysisId);
  }

  protected retryDeepAnalysis(analysisId: string): void {
    const detail = this.activeDeepAnalysisDetail();
    if (!detail) return;

    const query = detail.query;
    const repoId = this.repoId();
    if (!repoId) return;

    this.deepAnalysisService
      .createAnalysis(repoId, query)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (newAnalysis) => {
          this.deepAnalysisService
            .deleteAnalysis(analysisId)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe(() => {
              this.deepAnalyses.update((list) => list.filter((a) => a.id !== analysisId));
              this.refreshDeepAnalyses();
            });

          this.activeHistoryTab.set('deepAnalysis');
          this.selectDeepAnalysisById(newAnalysis.id);
        },
      });
  }

  protected deepAnalysisStatusClass(status: string): string {
    return `analysis__history-status--${status}`;
  }

  protected deepAnalysisStatusLabel(status: string): string {
    const keys: Record<string, string> = {
      pending: 'analysis.deepAnalysisStatusPending',
      running: 'analysis.deepAnalysisStatusRunning',
      completed: 'analysis.deepAnalysisStatusCompleted',
      failed: 'analysis.deepAnalysisStatusFailed',
    };
    return this.translateService.instant(keys[status] ?? status);
  }

  private selectDeepAnalysisById(analysisId: string): void {
    this.streamSub?.unsubscribe();
    this.activeChatId.set(null);
    this.isChatOpen.set(false);
    this.loadingMessages.set(false);
    this.activeDeepAnalysisId.set(analysisId);
    this.activeDeepAnalysisDetail.set(null);
    this.deepAnalysisPollSub?.unsubscribe();
    this.activeHistoryTab.set('deepAnalysis');
    this.updateUrl();

    this.loadDeepAnalysisDetail(analysisId);
  }

  private loadDeepAnalysisDetail(analysisId: string): void {
    this.deepAnalysisService
      .getAnalysis(analysisId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (detail) => {
          this.activeDeepAnalysisDetail.set(detail);
          if (detail.status === 'pending' || detail.status === 'running') {
            this.startDeepAnalysisPoll(analysisId);
          }
        },
        error: () => {
          this.activeDeepAnalysisDetail.set(null);
        },
      });
  }

  private startDeepAnalysisPoll(analysisId: string): void {
    this.deepAnalysisPollSub?.unsubscribe();
    this.deepAnalysisPollSub = new Subscription();
    const intervalId = setInterval(() => {
      if (this.activeDeepAnalysisId() !== analysisId) {
        clearInterval(intervalId);
        return;
      }
      this.deepAnalysisService
        .getAnalysis(analysisId)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (detail) => {
            this.activeDeepAnalysisDetail.set(detail);
            this.deepAnalyses.update((list) =>
              list.map((a) =>
                a.id === analysisId
                  ? { ...a, status: detail.status, finished_at: detail.finished_at }
                  : a
              )
            );
            if (detail.status !== 'pending' && detail.status !== 'running') {
              clearInterval(intervalId);
            }
          },
        });
    }, 10_000);
    this.deepAnalysisPollSub.add(() => clearInterval(intervalId));
  }

  private refreshDeepAnalyses(): void {
    const id = this.repoId();
    if (!id) return;
    this.deepAnalysisService
      .listAnalyses(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (analyses) => {
          this.deepAnalyses.set(analyses);
        },
      });
  }

  private finalizeStreamedMessage(assistantKey: string): void {
    const segments = this.streamSegments();
    let lastToolIndex = -1;
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].type === 'tool') {
        lastToolIndex = i;
        break;
      }
    }

    if (lastToolIndex === -1) return;

    const reasoning = segments.slice(0, lastToolIndex + 1);
    const afterTool = segments.slice(lastToolIndex + 1);
    const finalContent = afterTool
      .filter((s) => s.type === 'text')
      .map((s) => s.content)
      .join('');

    this.chatMessages.update((msgs) =>
      msgs.map((msg) =>
        msg.key === assistantKey ? { ...msg, content: finalContent, reasoning } : msg
      )
    );
  }

  private normalizeRole(role: string): ChatRole {
    if (role === 'human') return 'human';
    if (role === 'ai') return 'ai';
    if (role === 'user') return 'user';
    return 'assistant';
  }

  private parseMessageContext(
    content: string,
    role: ChatRole
  ): { content: string; context?: { fileName: string; nodeLabel: string } } {
    if (role === 'user' || role === 'human') {
      const match = content.match(/^\[Context:\s*(.+?)\s*>\s*(.+?)]\s*([\s\S]*)$/);
      if (match) {
        return {
          content: match[3],
          context: { fileName: match[1].trim(), nodeLabel: match[2].trim() },
        };
      }
    }
    return { content };
  }

  /**
   * Show the generated title on the thread list as soon as it is streamed.
   *
   * A thread created by this very stream is not in the list yet; the refresh
   * that runs when the stream completes picks it up.
   */
  private applyThreadTitle(title: string): void {
    const chatId = this.activeChatId();
    if (!chatId) return;
    this.threads.update((list) =>
      list.map((thread) => (thread.chat_id === chatId ? { ...thread, title } : thread))
    );
  }

  private refreshThreads(): void {
    const id = this.repoId();
    if (!id) return;
    this.loadingThreads.set(true);
    this.chatService
      .getThreads(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (threads) => {
          this.threads.set(threads);
          this.loadingThreads.set(false);
        },
        error: () => {
          this.loadingThreads.set(false);
        },
      });
  }

  private updateUrl(): void {
    const id = this.repoId();
    if (!id) return;

    const chatId = this.activeChatId();
    const analysisId = this.activeDeepAnalysisId();

    let path = `/project/${id}/analysis`;
    if (chatId) {
      path += `/chat/${chatId}`;
    } else if (analysisId) {
      path += `/deep_analysis/${analysisId}`;
    }

    this.location.replaceState(path);
  }
}
