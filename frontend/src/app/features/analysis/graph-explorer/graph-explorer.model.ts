import type cytoscape from 'cytoscape';
import type { FileGraphsResponse, ScopedGraph } from '../../../core/api';

export type GraphType = 'ast' | 'cfg' | 'dfg';

export interface GraphElements {
  nodes: cytoscape.ElementDefinition[];
  edges: cytoscape.ElementDefinition[];
}

// Detects graphs produced by a pre-`return_type_fqn`/`branch` backend. Such data
// predates a breaking pipeline change and can only be upgraded by re-analysing the
// project, so the UI surfaces a "re-upload as a new project" warning.
export function isLegacyGraphData(data: FileGraphsResponse): boolean {
  return hasLegacyAstMethod(data.ast) || hasLegacyCfgNode(data.cfg);
}

function hasLegacyAstMethod(ast: Record<string, unknown> | null): boolean {
  if (!ast) return false;
  const root =
    !('file' in ast) && ast['content'] && typeof ast['content'] === 'object'
      ? (ast['content'] as Record<string, unknown>)
      : ast;

  const containers = [root['classes'], root['interfaces']]
    .filter(Array.isArray)
    .flat() as Record<string, unknown>[];

  for (const container of containers) {
    const methods = container['methods'];
    if (!Array.isArray(methods)) continue;
    for (const method of methods as Record<string, unknown>[]) {
      const isNewFormat = 'return_type_fqn' in method || 'signature_simple' in method;
      if (!isNewFormat && 'return_type' in method) return true;
    }
  }
  return false;
}

function hasLegacyCfgNode(cfg: ScopedGraph[]): boolean {
  for (const graph of cfg) {
    const nodes = graph.content['nodes'];
    if (!Array.isArray(nodes)) continue;
    for (const node of nodes as Record<string, unknown>[]) {
      if (node['type'] === 'condition') return true;
    }
  }
  return false;
}

// CFG/DFG scopes are fully-qualified names (e.g.
// "method:fr.icdc.dei.banque.titan.adminbusiness.AbstractDBUnitTest#setUp():void")
// to disambiguate overloaded Java methods. Keep the signature (incl. args) so
// overloads stay distinct, but drop the kind prefix and package path for display.
export function formatScopeLabel(scope: string | null): string {
  if (!scope) return '(global)';
  const withoutKind = scope.replace(/^[a-zA-Z_]+:/, '');
  const hashIndex = withoutKind.indexOf('#');
  if (hashIndex === -1) return withoutKind;
  const fqcn = withoutKind.slice(0, hashIndex);
  const signature = withoutKind.slice(hashIndex + 1);
  const simpleClass = fqcn.split('.').pop() ?? fqcn;
  return `${simpleClass}#${signature}`;
}

export function convertAstToElements(raw: Record<string, unknown>): GraphElements {
  // The API exposes the AST document's `content` directly; unwrap in case a
  // full document ({ content: {...} }) is handed over instead.
  const ast =
    !('file' in raw) && raw['content'] && typeof raw['content'] === 'object'
      ? (raw['content'] as Record<string, unknown>)
      : raw;

  const nodes: cytoscape.ElementDefinition[] = [];
  const edges: cytoscape.ElementDefinition[] = [];
  let counter = 0;

  const addNode = (label: string, nodeType: string, parentId?: string): string => {
    const id = `ast-${counter++}`;
    nodes.push({ data: { id, label, nodeType } });
    if (parentId) {
      edges.push({ data: { id: `${parentId}->${id}`, source: parentId, target: id } });
    }
    return id;
  };

  const simpleName = (fqn: string): string => fqn.split('.').pop() ?? fqn;

  const methodLabel = (method: Record<string, unknown>): string => {
    const signature =
      (method['signature_simple'] as string) ?? `${(method['name'] as string) ?? ''}()`;
    const ret = simpleName((method['return_type_fqn'] as string) ?? '');
    return ret ? `${signature}: ${ret}` : signature;
  };

  const MAX_STMT_DEPTH = 6;

  const describeStatement = (stmt: unknown): { label: string; children: unknown } => {
    if (typeof stmt === 'string') return { label: stmt, children: null };
    if (stmt && typeof stmt === 'object') {
      const s = stmt as Record<string, unknown>;
      const kindVal = s['type'] ?? s['kind'] ?? s['node'];
      const textVal =
        s['text'] ?? s['code'] ?? s['label'] ?? s['expression'] ?? s['value'] ?? s['name'];
      const kind = kindVal != null ? String(kindVal) : '';
      const text = textVal != null ? String(textVal) : '';
      const label = [kind, text].filter(Boolean).join(': ') || 'statement';
      return { label, children: s['statements'] ?? s['body'] ?? null };
    }
    return { label: String(stmt), children: null };
  };

  const addStatements = (stmts: unknown, parentId: string, depth: number): void => {
    if (depth > MAX_STMT_DEPTH || !Array.isArray(stmts)) return;
    for (const stmt of stmts) {
      const { label, children } = describeStatement(stmt);
      const id = addNode(label, 'statement', parentId);
      addStatements(children, id, depth + 1);
    }
  };

  // Root: file node
  const fileName = (ast['file'] as string) ?? 'file';
  const rootId = addNode(fileName.split('/').pop() ?? fileName, 'file');

  // Package
  const pkg = ast['package'] as string | undefined;
  if (pkg) {
    addNode(pkg, 'package', rootId);
  }

  // Imports
  const imports = ast['imports'] as Record<string, unknown>[] | undefined;
  if (Array.isArray(imports) && imports.length > 0) {
    const importsGroupId = addNode(`imports (${imports.length})`, 'group', rootId);
    for (const imp of imports) {
      const path = (imp['path'] as string) ?? '';
      addNode(path.split('.').pop() ?? path, 'import', importsGroupId);
    }
  }

  // Classes
  const classes = ast['classes'] as Record<string, unknown>[] | undefined;
  if (Array.isArray(classes)) {
    for (const cls of classes) {
      const className = (cls['name'] as string) ?? 'class';
      const classId = addNode(className, 'class', rootId);

      // Fields
      const fields = cls['fields'] as Record<string, unknown>[] | undefined;
      if (Array.isArray(fields)) {
        for (const field of fields) {
          const fname = (field['name'] as string) ?? '';
          const ftype = simpleName(
            (field['type_simple'] as string) ?? (field['type_fqn'] as string) ?? ''
          );
          addNode(ftype ? `${fname}: ${ftype}` : fname, 'field', classId);
        }
      }

      // Methods
      const methods = cls['methods'] as Record<string, unknown>[] | undefined;
      if (Array.isArray(methods)) {
        for (const method of methods) {
          const methodId = addNode(methodLabel(method), 'method', classId);
          addStatements(method['statements'], methodId, 0);
        }
      }
    }
  }

  // Interfaces
  const interfaces = ast['interfaces'] as Record<string, unknown>[] | undefined;
  if (Array.isArray(interfaces)) {
    for (const iface of interfaces) {
      const ifaceName = (iface['name'] as string) ?? 'interface';
      const ifaceId = addNode(ifaceName, 'interface', rootId);

      const methods = iface['methods'] as Record<string, unknown>[] | undefined;
      if (Array.isArray(methods)) {
        for (const method of methods) {
          const methodId = addNode(methodLabel(method), 'method', ifaceId);
          addStatements(method['statements'], methodId, 0);
        }
      }
    }
  }

  // Enums
  const enums = ast['enums'] as Record<string, unknown>[] | undefined;
  if (Array.isArray(enums)) {
    for (const en of enums) {
      addNode((en['name'] as string) ?? 'enum', 'enum', rootId);
    }
  }

  return { nodes, edges };
}

export function convertScopedGraphToElements(content: Record<string, unknown>): GraphElements {
  const rawNodes = (content['nodes'] as Record<string, unknown>[]) ?? [];
  const rawEdges = (content['edges'] as Record<string, unknown>[]) ?? [];

  const nodeIds = new Set(rawNodes.map((n) => String(n['id'])));

  // CFG nodes carry a `label`; DFG nodes instead carry a `variable` plus an
  // `operation` (def/use) that must be shown to tell repeated accesses apart.
  const nodeLabel = (n: Record<string, unknown>): string => {
    if (n['label'] != null) return String(n['label']);
    if (n['variable'] != null) {
      return [n['operation'], n['variable']].filter(Boolean).map(String).join(' ');
    }
    return String(n['name'] ?? n['id']);
  };

  const nodes = rawNodes.map((n) => ({
    data: {
      id: String(n['id']),
      label: nodeLabel(n),
      nodeType: String(n['type'] ?? ''),
    },
  }));

  const edges = rawEdges
    .filter((e) => {
      const source = String(e['from'] ?? e['source']);
      const target = String(e['to'] ?? e['target']);
      return nodeIds.has(source) && nodeIds.has(target);
    })
    .map((e, i) => {
      const source = String(e['from'] ?? e['source']);
      const target = String(e['to'] ?? e['target']);
      return {
        data: {
          id: `e-${source}-${target}-${i}`,
          source,
          target,
          label: String(e['label'] ?? e['type'] ?? ''),
        },
      };
    });

  return { nodes, edges };
}
