import { readdir, readFile, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import ts from "typescript";

type BoundaryRule =
  | "playwright-api-request-context"
  | "playwright-bound-request"
  | "playwright-computed-request"
  | "playwright-request-new-context"
  | "opencli-import";

export type BoundaryViolation = {
  file: string;
  line: number;
  column: number;
  rule: BoundaryRule;
  message: string;
  expression: string;
};

const BOUND_REQUEST_OWNERS = new Set(["page", "browserContext", "context", "playwright"]);

export function findBoundaryViolationsInSource(
  sourceText: string,
  fileName = "fixture.ts"
): BoundaryViolation[] {
  const sourceFile = ts.createSourceFile(
    fileName,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS
  );
  const violations: BoundaryViolation[] = [];
  const playwrightNamespaces = findPlaywrightNamespaceImports(sourceFile);

  function addViolation(node: ts.Node, rule: BoundaryRule, message: string): void {
    const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    violations.push({
      file: fileName,
      line: position.line + 1,
      column: position.character + 1,
      rule,
      message,
      expression: node.getText(sourceFile),
    });
  }

  function visit(node: ts.Node): void {
    if (ts.isImportDeclaration(node)) {
      checkImport(node, sourceFile, addViolation);
    }

    if (ts.isBindingElement(node)) {
      checkBindingElement(node, sourceFile, addViolation);
    }

    if (ts.isCallExpression(node)) {
      checkCallExpression(node, sourceFile, addViolation);
    }

    if (isAPIRequestContextReference(node, playwrightNamespaces)) {
      addViolation(
        node,
        "playwright-api-request-context",
        "APIRequestContext is outside the Liepin worker boundary"
      );
    }

    if (ts.isPropertyAccessExpression(node)) {
      checkPropertyAccess(node, sourceFile, addViolation);
    }

    if (ts.isElementAccessExpression(node)) {
      checkElementAccess(node, sourceFile, addViolation);
    }

    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return violations;
}

export async function findBoundaryViolations(paths = defaultScanRoots()): Promise<BoundaryViolation[]> {
  const files = await collectTypeScriptFiles(paths);
  const groups = await Promise.all(
    files.map(async (file) => findBoundaryViolationsInSource(await readFile(file, "utf8"), file))
  );
  return groups.flat();
}

async function main(paths: string[]): Promise<number> {
  const violations = await findBoundaryViolations(paths.length > 0 ? paths : defaultScanRoots());
  if (violations.length === 0) {
    console.log("Liepin worker boundary check passed");
    return 0;
  }

  for (const violation of violations) {
    console.error(
      `${violation.file}:${violation.line}:${violation.column} ${violation.rule}: ${violation.message} (${violation.expression})`
    );
  }
  return 1;
}

function checkImport(
  node: ts.ImportDeclaration,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void
): void {
  const moduleName = node.moduleSpecifier;
  if (!ts.isStringLiteral(moduleName)) {
    return;
  }

  if (moduleName.text.toLowerCase().includes("opencli")) {
    addViolation(node, "opencli-import", "OpenCLI imports are forbidden in the Liepin worker");
  }

  if (moduleName.text !== "playwright" && moduleName.text !== "@playwright/test") {
    return;
  }

  const namedBindings = node.importClause?.namedBindings;
  if (!namedBindings || !ts.isNamedImports(namedBindings)) {
    return;
  }

  for (const element of namedBindings.elements) {
    const importedName = element.propertyName?.text ?? element.name.text;
    if (importedName === "APIRequestContext") {
      addViolation(
        element,
        "playwright-api-request-context",
        "APIRequestContext is outside the Liepin worker boundary"
      );
    }
  }
}

function findPlaywrightNamespaceImports(sourceFile: ts.SourceFile): Set<string> {
  const namespaces = new Set<string>();

  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) {
      continue;
    }
    if (
      statement.moduleSpecifier.text !== "playwright" &&
      statement.moduleSpecifier.text !== "@playwright/test"
    ) {
      continue;
    }

    const namedBindings = statement.importClause?.namedBindings;
    if (namedBindings && ts.isNamespaceImport(namedBindings)) {
      namespaces.add(namedBindings.name.text);
    }
  }

  return namespaces;
}

function isAPIRequestContextReference(
  node: ts.Node,
  playwrightNamespaces: Set<string>
): node is ts.TypeReferenceNode {
  if (!ts.isTypeReferenceNode(node)) {
    return false;
  }

  const typeName = node.typeName;
  if (ts.isIdentifier(typeName)) {
    return typeName.text === "APIRequestContext";
  }

  return (
    ts.isQualifiedName(typeName) &&
    ts.isIdentifier(typeName.left) &&
    playwrightNamespaces.has(typeName.left.text) &&
    typeName.right.text === "APIRequestContext"
  );
}

function checkBindingElement(
  node: ts.BindingElement,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void
): void {
  if (!isRequestBindingElement(node) || !isDestructuredFromBoundRequestOwner(node)) {
    return;
  }

  addViolation(
    node,
    "playwright-bound-request",
    `${node.getText(sourceFile)} destructures Playwright request outside the browser boundary`
  );
}

function checkCallExpression(
  node: ts.CallExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void
): void {
  const firstArg = node.arguments[0];
  if (!isOpenCliModuleName(firstArg)) {
    return;
  }

  if (node.expression.kind === ts.SyntaxKind.ImportKeyword || isRequireCall(node.expression)) {
    addViolation(
      node,
      "opencli-import",
      `${node.getText(sourceFile)} dynamically imports forbidden OpenCLI code`
    );
  }
}

function checkPropertyAccess(
  node: ts.PropertyAccessExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void
): void {
  if (node.name.text === "request" && isBoundRequestOwner(node.expression)) {
    addViolation(
      node,
      "playwright-bound-request",
      `${node.getText(sourceFile)} uses Playwright request outside the browser boundary`
    );
  }

  if (node.name.text === "newContext" && isPlaywrightRequestExpression(node.expression)) {
    addViolation(
      node,
      "playwright-request-new-context",
      `${node.getText(sourceFile)} creates a Playwright API request context`
    );
  }
}

function checkElementAccess(
  node: ts.ElementAccessExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void
): void {
  if (!isRequestStringLiteral(node.argumentExpression) || !isBoundRequestOwner(node.expression)) {
    return;
  }

  addViolation(
    node,
    "playwright-computed-request",
    `${node.getText(sourceFile)} uses computed Playwright request access`
  );
}

function isBoundRequestOwner(expression: ts.Expression): boolean {
  return ts.isIdentifier(expression) && BOUND_REQUEST_OWNERS.has(expression.text);
}

function isPlaywrightRequestExpression(expression: ts.Expression): boolean {
  if (ts.isIdentifier(expression)) {
    return expression.text === "request";
  }

  return (
    ts.isPropertyAccessExpression(expression) &&
    expression.name.text === "request" &&
    isBoundRequestOwner(expression.expression)
  );
}

function isRequestStringLiteral(expression: ts.Expression | undefined): boolean {
  return (
    expression !== undefined &&
    ts.isStringLiteralLike(expression) &&
    expression.text === "request"
  );
}

function isRequestBindingElement(node: ts.BindingElement): boolean {
  if (node.propertyName !== undefined) {
    return ts.isIdentifier(node.propertyName) && node.propertyName.text === "request";
  }

  return ts.isIdentifier(node.name) && node.name.text === "request";
}

function isDestructuredFromBoundRequestOwner(node: ts.BindingElement): boolean {
  const bindingPattern = node.parent;
  const declaration = bindingPattern.parent;

  return (
    ts.isObjectBindingPattern(bindingPattern) &&
    ts.isVariableDeclaration(declaration) &&
    declaration.initializer !== undefined &&
    isBoundRequestOwner(declaration.initializer)
  );
}

function isOpenCliModuleName(node: ts.Node | undefined): node is ts.StringLiteral {
  return (
    node !== undefined &&
    ts.isStringLiteral(node) &&
    node.text.toLowerCase().includes("opencli")
  );
}

function isRequireCall(expression: ts.Expression): boolean {
  return ts.isIdentifier(expression) && expression.text === "require";
}

async function collectTypeScriptFiles(paths: string[]): Promise<string[]> {
  const groups = await Promise.all(paths.map((path) => collectPath(resolve(path))));
  return groups.flat().filter((file) => /\.(?:ts|tsx)$/.test(file));
}

async function collectPath(path: string): Promise<string[]> {
  const pathStat = await stat(path);
  if (pathStat.isFile()) {
    return [path];
  }

  const entries = await readdir(path, { withFileTypes: true });
  const groups = await Promise.all(
    entries
      .filter((entry) => entry.name !== "node_modules")
      .map((entry) => collectPath(join(path, entry.name)))
  );
  return groups.flat();
}

function defaultScanRoots(): string[] {
  return ["src", "tests", "scripts"];
}

if (import.meta.main) {
  const exitCode = await main(Bun.argv.slice(2));
  process.exit(exitCode);
}
