import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { DatePipe, Location } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { map, Subscription, switchMap } from 'rxjs';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import {
  ChatMessage,
  ChatMessageSegment,
  ChatRole,
  ChatService,
  ChatThread,
  DeepAnalysis,
  DeepAnalysisDetail,
  DeepAnalysisService,
  RepoStore,
} from '../../core/api';
import { BreadcrumbService } from '../../shared/breadcrumb.service';
import { MarkdownPipe } from '../../shared/markdown.pipe';
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
  imports: [GraphExplorer, DeepAnalysisDialog, DeepAnalysisDetailComponent, FormsModule, DatePipe, MarkdownPipe, MermaidDirective, TranslateModule, UiButton, UiSpinner],
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
  private readonly translateService = inject(TranslateService);
  private readonly analysisActionService = inject(AnalysisActionService);

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

  protected readonly isZipUpload = computed(() => !this.repo()?.repo_branch);

  protected readonly linesOfCode = signal('12.5K');
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
  protected readonly activeTools = signal<Map<string, string>>(new Map());
  protected readonly hasActiveTool = computed(() => this.activeTools().size > 0);
  protected readonly streamSegments = signal<ChatMessageSegment[]>([]);
  protected readonly expandedReasoning = signal<Set<number>>(new Set());

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
    this.activeTools.set(new Map());
    this.streamSegments.set([]);
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
    this.activeTools.set(new Map());
    this.streamSegments.set([]);
    this.activeHistoryTab.set('chat');
    this.updateUrl();

    this.chatService
      .getMessages(chatId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          const messages: ChatMessage[] = res.messages.map((m) => {
            const role = this.normalizeRole(m.role);
            const parsed = this.parseMessageContext(m.content, role);
            return {
              role,
              ...parsed,
              content: this.stripTitleJson(parsed.content, role),
            };
          });
          this.chatMessages.set(messages);
          this.loadingMessages.set(false);
        },
        error: () => {
          this.chatMessages.set([
            {
              role: 'assistant',
              content: this.translateService.instant('analysis.chatError'),
            },
          ]);
          this.loadingMessages.set(false);
        },
      });
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

    const ctx = this.chatContext();
    const fullMessage = ctx ? `[Context: ${ctx.fileName} > ${ctx.nodeLabel}] ${text}` : text;

    this.chatMessages.update((msgs) => [
      ...msgs,
      { role: 'user', content: text, timestamp: new Date(), context: ctx ?? undefined },
    ]);
    this.chatInputValue.set('');
    this.isStreaming.set(true);
    this.streamSegments.set([]);

    const assistantMsg: ChatMessage = { role: 'assistant', content: '', timestamp: new Date() };
    this.chatMessages.update((msgs) => [...msgs, assistantMsg]);
    const assistantIndex = this.chatMessages().length - 1;

    this.streamSub?.unsubscribe();

    const chatId = this.activeChatId();
    const stream$ = chatId
      ? this.chatService.resumeChat(chatId, fullMessage)
      : this.chatService.createChat(this.repoId()!, fullMessage);

    this.streamSub = stream$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (event) => {
        if (event.type === 'chat_id') {
          this.activeChatId.set(event.chatId);
          this.updateUrl();
        } else if (event.type === 'tool_start') {
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
        } else {
          this.streamSegments.update((segs) => {
            const last = segs[segs.length - 1];
            if (last && last.type === 'text') {
              const updated = [...segs];
              updated[updated.length - 1] = { ...last, content: last.content + event.content };
              return updated;
            }
            return [...segs, { type: 'text', content: event.content }];
          });
          this.chatMessages.update((msgs) => {
            const updated = [...msgs];
            updated[assistantIndex] = {
              ...updated[assistantIndex],
              content: updated[assistantIndex].content + event.content,
            };
            return updated;
          });
        }
      },
      error: () => {
        this.chatMessages.update((msgs) => {
          const updated = [...msgs];
          updated[assistantIndex] = {
            ...updated[assistantIndex],
            content:
              updated[assistantIndex].content ||
              this.translateService.instant('analysis.chatError'),
          };
          return updated;
        });
        this.isStreaming.set(false);
        this.activeTools.set(new Map());
      },
      complete: () => {
        this.isStreaming.set(false);
        this.activeTools.set(new Map());
        this.finalizeStreamedMessage(assistantIndex);
        this.stripTitleFromLastMessage();
        this.refreshThreads();
      },
    });
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

  protected toggleReasoning(index: number): void {
    this.expandedReasoning.update((set) => {
      const next = new Set(set);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  protected isReasoningExpanded(index: number): boolean {
    return this.expandedReasoning().has(index);
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

  private finalizeStreamedMessage(assistantIndex: number): void {
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

    this.chatMessages.update((msgs) => {
      const updated = [...msgs];
      updated[assistantIndex] = {
        ...updated[assistantIndex],
        content: finalContent,
        reasoning,
      };
      return updated;
    });
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

  private stripTitleJson(content: string, role: ChatRole): string {
    if (this.isUserRole(role)) return content;
    return content.replace(/\s*\{"title"\s*:\s*"[^"]*"\}\s*$/, '').trimEnd();
  }

  private stripTitleFromLastMessage(): void {
    const msgs = this.chatMessages();
    if (msgs.length === 0) return;
    const lastMsg = msgs[msgs.length - 1];
    if (this.isUserRole(lastMsg.role)) return;

    const stripped = this.stripTitleJson(lastMsg.content, lastMsg.role);
    if (stripped !== lastMsg.content) {
      this.chatMessages.update((list) => {
        const updated = [...list];
        updated[updated.length - 1] = { ...updated[updated.length - 1], content: stripped };
        return updated;
      });
    }
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
