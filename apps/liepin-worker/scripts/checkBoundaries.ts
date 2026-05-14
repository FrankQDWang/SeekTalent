import { readFileSync } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import ts from "typescript";

type BoundaryRule =
  | "playwright-api-request-context"
  | "playwright-bound-request"
  | "playwright-computed-request"
  | "playwright-request-new-context"
  | "provider-network-inspection"
  | "provider-network-interception"
  | "provider-script-evaluation"
  | "provider-cookie-header-storage"
  | "provider-cdp-access"
  | "provider-in-page-network"
  | "opencli-import";

type ScanProfile = "provider_action" | "session_lifecycle" | "test_fixture";

type BoundaryRegistry = {
  typescript_forbidden_operation_markers?: string[];
  typescript_provider_action_forbidden_operation_markers?: string[];
  typescript_session_lifecycle_allowed_operation_markers?: string[];
};

export type BoundaryViolation = {
  file: string;
  line: number;
  column: number;
  rule: BoundaryRule;
  message: string;
  expression: string;
};

const BOUND_REQUEST_OWNERS = new Set(["page", "browserContext", "context", "playwright"]);
const DOKOBOT_NETWORK_TOOL_NAMES = new Set(["list_network_requests", "get_network_request"]);
const DOKOBOT_SCRIPT_TOOL_NAMES = new Set(["evaluate_script"]);
const ROUTE_INTERCEPTION_METHODS = new Set(["route", "fetch", "continue", "fulfill"]);
const SCRIPT_EVALUATION_METHODS = new Set(["evaluate", "evaluateHandle", "addInitScript"]);
const COOKIE_HEADER_STORAGE_METHODS = new Set(["addCookies", "setExtraHTTPHeaders", "storageState"]);
const CDP_METHODS = new Set(["newCDPSession"]);
const IN_PAGE_NETWORK_GLOBALS = new Set(["fetch", "XMLHttpRequest"]);
const REGISTRY = loadBoundaryRegistry();
const TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS = new Set(
  REGISTRY.typescript_provider_action_forbidden_operation_markers ?? []
);
const TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS = new Set(
  REGISTRY.typescript_session_lifecycle_allowed_operation_markers ?? []
);

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
  const playwrightRequestAliases = findPlaywrightRequestImports(sourceFile);
  const boundRequestOwners = findBoundRequestOwnerAliases(sourceFile);
  const scanProfile = scanProfileForFile(fileName);

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
      checkBindingElement(node, sourceFile, addViolation, boundRequestOwners);
    }

    if (ts.isCallExpression(node)) {
      checkCallExpression(node, sourceFile, addViolation, scanProfile);
    }

    if (ts.isNewExpression(node)) {
      checkNewExpression(node, sourceFile, addViolation, scanProfile);
    }

    if (isAPIRequestContextReference(node, playwrightNamespaces)) {
      addViolation(
        node,
        "playwright-api-request-context",
        "APIRequestContext is outside the Liepin worker boundary"
      );
    }

    if (ts.isPropertyAccessExpression(node)) {
      checkPropertyAccess(
        node,
        sourceFile,
        addViolation,
        playwrightRequestAliases,
        playwrightNamespaces,
        boundRequestOwners,
        scanProfile
      );
    }

    if (ts.isElementAccessExpression(node)) {
      checkElementAccess(node, sourceFile, addViolation, boundRequestOwners, scanProfile);
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

function findPlaywrightRequestImports(sourceFile: ts.SourceFile): Set<string> {
  const aliases = new Set<string>(["request"]);

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
    if (!namedBindings || !ts.isNamedImports(namedBindings)) {
      continue;
    }

    for (const element of namedBindings.elements) {
      const importedName = element.propertyName?.text ?? element.name.text;
      if (importedName === "request") {
        aliases.add(element.name.text);
      }
    }
  }

  return aliases;
}

function findBoundRequestOwnerAliases(sourceFile: ts.SourceFile): Set<string> {
  const aliases = new Set(BOUND_REQUEST_OWNERS);

  function visit(node: ts.Node): void {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer !== undefined &&
      ts.isIdentifier(node.initializer) &&
      aliases.has(node.initializer.text)
    ) {
      aliases.add(node.name.text);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return aliases;
}

function isAPIRequestContextReference(
  node: ts.Node,
  playwrightNamespaces: Set<string>
): node is ts.TypeReferenceNode | ts.ImportTypeNode {
  if (ts.isImportTypeNode(node)) {
    return isAPIRequestContextImportType(node);
  }

  if (ts.isTypeReferenceNode(node)) {
    return isAPIRequestContextEntityName(node.typeName, playwrightNamespaces);
  }

  return false;
}

function isAPIRequestContextImportType(node: ts.ImportTypeNode): boolean {
  const argument = node.argument;
  if (!ts.isLiteralTypeNode(argument) || !ts.isStringLiteral(argument.literal)) {
    return false;
  }
  if (argument.literal.text !== "playwright" && argument.literal.text !== "@playwright/test") {
    return false;
  }
  return node.qualifier !== undefined && isAPIRequestContextEntityName(node.qualifier, new Set());
}

function isAPIRequestContextEntityName(
  typeName: ts.EntityName,
  playwrightNamespaces: Set<string>
): boolean {
  if (ts.isIdentifier(typeName)) {
    return typeName.text === "APIRequestContext";
  }

  return (
    ts.isIdentifier(typeName.left) &&
    playwrightNamespaces.has(typeName.left.text) &&
    typeName.right.text === "APIRequestContext"
  );
}

function checkBindingElement(
  node: ts.BindingElement,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  boundRequestOwners: Set<string>
): void {
  if (!isRequestBindingElement(node) || !isDestructuredFromBoundRequestOwner(node, boundRequestOwners)) {
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
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  scanProfile: ScanProfile
): void {
  const firstArg = node.arguments[0];
  if (isOpenCliModuleName(firstArg) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || isRequireCall(node.expression))) {
    addViolation(
      node,
      "opencli-import",
      `${node.getText(sourceFile)} dynamically imports forbidden OpenCLI code`
    );
  }

  checkCallBoundary(node, sourceFile, addViolation, scanProfile);
}

function checkNewExpression(
  node: ts.NewExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  scanProfile: ScanProfile
): void {
  if (
    scanProfile === "provider_action" &&
    ts.isIdentifier(node.expression) &&
    IN_PAGE_NETWORK_GLOBALS.has(node.expression.text)
  ) {
    addViolation(
      node.expression,
      "provider-in-page-network",
      `${node.expression.getText(sourceFile)} is forbidden in provider action code`
    );
  }
}

function checkCallBoundary(
  node: ts.CallExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  scanProfile: ScanProfile
): void {
  const expression = node.expression;
  if (ts.isIdentifier(expression)) {
    checkIdentifierCall(expression, sourceFile, addViolation, scanProfile);
    return;
  }

  if (!ts.isPropertyAccessExpression(expression)) {
    return;
  }

  const name = expression.name.text;
  const owner = expression.expression.getText(sourceFile);
  const expressionText = expression.getText(sourceFile);

  if (DOKOBOT_NETWORK_TOOL_NAMES.has(name)) {
    addViolation(expression, "provider-network-inspection", `${expressionText} inspects provider network requests`);
    return;
  }
  if (DOKOBOT_SCRIPT_TOOL_NAMES.has(name)) {
    addViolation(expression, "provider-script-evaluation", `${expressionText} evaluates arbitrary page script`);
    return;
  }
  if (ROUTE_INTERCEPTION_METHODS.has(name) && isProviderRouteInterceptionOwner(owner, scanProfile)) {
    addViolation(expression, "provider-network-interception", `${expressionText} intercepts provider network traffic`);
    return;
  }
  if (name === "waitForResponse" && scanProfile !== "test_fixture") {
    addViolation(expression, "provider-network-inspection", `${expressionText} observes provider network responses`);
    return;
  }
  if (name === "on" && isForbiddenNetworkEventCall(node) && scanProfile !== "test_fixture") {
    addViolation(expression, "provider-network-inspection", `${expressionText} observes provider network events`);
    return;
  }
  if (SCRIPT_EVALUATION_METHODS.has(name) && scanProfile !== "test_fixture") {
    addViolation(expression, "provider-script-evaluation", `${expressionText} evaluates arbitrary page script`);
    return;
  }
  if (COOKIE_HEADER_STORAGE_METHODS.has(name) && isForbiddenStorageCall(name, expressionText, scanProfile)) {
    addViolation(expression, "provider-cookie-header-storage", `${expressionText} manipulates provider cookies, headers, or storage`);
    return;
  }
  if (CDP_METHODS.has(name)) {
    addViolation(expression, "provider-cdp-access", `${expressionText} opens a CDP session`);
  }
}

function checkPropertyAccess(
  node: ts.PropertyAccessExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  playwrightRequestAliases: Set<string>,
  playwrightNamespaces: Set<string>,
  boundRequestOwners: Set<string>,
  scanProfile: ScanProfile
): void {
  if (node.name.text === "request" && isBoundRequestOwner(node.expression, boundRequestOwners)) {
    addViolation(
      node,
      "playwright-bound-request",
      `${node.getText(sourceFile)} uses Playwright request outside the browser boundary`
    );
  }

  if (
    node.name.text === "newContext" &&
    isPlaywrightRequestExpression(node.expression, playwrightRequestAliases, playwrightNamespaces)
  ) {
    addViolation(
      node,
      "playwright-request-new-context",
      `${node.getText(sourceFile)} creates a Playwright API request context`
    );
  }

  if (node.name.text === "CDPSession") {
    addViolation(
      node,
      "provider-cdp-access",
      `${node.getText(sourceFile)} references a CDP session type`
    );
  }

  if (
    scanProfile !== "test_fixture" &&
    ts.isIdentifier(node.expression) &&
    IN_PAGE_NETWORK_GLOBALS.has(node.expression.text)
  ) {
    addViolation(
      node.expression,
      "provider-in-page-network",
      `${node.expression.getText(sourceFile)} is forbidden in provider action code`
    );
  }
}

function checkElementAccess(
  node: ts.ElementAccessExpression,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  boundRequestOwners: Set<string>,
  scanProfile: ScanProfile
): void {
  const propertyName = stringLiteralText(node.argumentExpression);
  if (propertyName === null || !isBoundRequestOwner(node.expression, boundRequestOwners)) {
    return;
  }

  if (propertyName === "request") {
    addViolation(
      node,
      "playwright-computed-request",
      `${node.getText(sourceFile)} uses computed Playwright request access`
    );
    return;
  }

  if (scanProfile === "test_fixture") {
    return;
  }

  if (ROUTE_INTERCEPTION_METHODS.has(propertyName)) {
    addViolation(node, "provider-network-interception", `${node.getText(sourceFile)} intercepts provider network traffic`);
    return;
  }
  if (propertyName === "waitForResponse") {
    addViolation(node, "provider-network-inspection", `${node.getText(sourceFile)} observes provider network responses`);
    return;
  }
  if (SCRIPT_EVALUATION_METHODS.has(propertyName)) {
    addViolation(node, "provider-script-evaluation", `${node.getText(sourceFile)} evaluates arbitrary page script`);
    return;
  }
  if (COOKIE_HEADER_STORAGE_METHODS.has(propertyName) && isForbiddenStorageCall(propertyName, propertyName, scanProfile)) {
    addViolation(node, "provider-cookie-header-storage", `${node.getText(sourceFile)} manipulates provider cookies, headers, or storage`);
    return;
  }
  if (CDP_METHODS.has(propertyName)) {
    addViolation(node, "provider-cdp-access", `${node.getText(sourceFile)} opens a CDP session`);
  }
}

function isBoundRequestOwner(expression: ts.Expression, boundRequestOwners: Set<string>): boolean {
  return ts.isIdentifier(expression) && boundRequestOwners.has(expression.text);
}

function isPlaywrightRequestExpression(
  expression: ts.Expression,
  playwrightRequestAliases: Set<string>,
  playwrightNamespaces: Set<string>
): boolean {
  if (ts.isIdentifier(expression)) {
    return playwrightRequestAliases.has(expression.text);
  }

  return (
    ts.isPropertyAccessExpression(expression) &&
    expression.name.text === "request" &&
    isPlaywrightRequestOwner(expression.expression, playwrightNamespaces)
  );
}

function isPlaywrightRequestOwner(
  expression: ts.Expression,
  playwrightNamespaces: Set<string>
): boolean {
  return (
    isBoundRequestOwner(expression, BOUND_REQUEST_OWNERS) ||
    (ts.isIdentifier(expression) && playwrightNamespaces.has(expression.text))
  );
}

function isRequestStringLiteral(expression: ts.Expression | undefined): boolean {
  return stringLiteralText(expression) === "request";
}

function stringLiteralText(expression: ts.Expression | undefined): string | null {
  return (
    expression !== undefined &&
    ts.isStringLiteralLike(expression)
  ) ? expression.text : null;
}

function isRequestBindingElement(node: ts.BindingElement): boolean {
  if (node.propertyName !== undefined) {
    return isRequestPropertyName(node.propertyName);
  }

  return ts.isIdentifier(node.name) && node.name.text === "request";
}

function isRequestPropertyName(propertyName: ts.PropertyName): boolean {
  if (ts.isIdentifier(propertyName)) {
    return propertyName.text === "request";
  }

  if (ts.isComputedPropertyName(propertyName)) {
    return isRequestStringLiteral(propertyName.expression);
  }

  return false;
}

function isDestructuredFromBoundRequestOwner(node: ts.BindingElement, boundRequestOwners: Set<string>): boolean {
  const bindingPattern = node.parent;
  const declaration = bindingPattern.parent;

  return (
    ts.isObjectBindingPattern(bindingPattern) &&
    ts.isVariableDeclaration(declaration) &&
    declaration.initializer !== undefined &&
    isBoundRequestOwner(declaration.initializer, boundRequestOwners)
  );
}

function isOpenCliModuleName(node: ts.Node | undefined): boolean {
  return (
    node !== undefined &&
    (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
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
  return ["src"];
}

function checkIdentifierCall(
  expression: ts.Identifier,
  sourceFile: ts.SourceFile,
  addViolation: (node: ts.Node, rule: BoundaryRule, message: string) => void,
  scanProfile: ScanProfile
): void {
  if (DOKOBOT_NETWORK_TOOL_NAMES.has(expression.text)) {
    addViolation(expression, "provider-network-inspection", `${expression.text} inspects provider network requests`);
    return;
  }
  if (DOKOBOT_SCRIPT_TOOL_NAMES.has(expression.text)) {
    addViolation(expression, "provider-script-evaluation", `${expression.text} evaluates arbitrary page script`);
    return;
  }
  if (scanProfile !== "test_fixture" && IN_PAGE_NETWORK_GLOBALS.has(expression.text)) {
    addViolation(
      expression,
      "provider-in-page-network",
      `${expression.getText(sourceFile)} is forbidden in provider action code`
    );
  }
}

function isProviderRouteInterceptionOwner(owner: string, scanProfile: ScanProfile): boolean {
  if (scanProfile === "test_fixture") {
    return false;
  }
  return owner === "page" || owner === "browserContext" || owner === "context" || owner === "route";
}

function isForbiddenNetworkEventCall(node: ts.CallExpression): boolean {
  const firstArg = node.arguments[0];
  return (
    firstArg !== undefined &&
    ts.isStringLiteralLike(firstArg) &&
    (firstArg.text === "request" || firstArg.text === "response")
  );
}

function isForbiddenStorageCall(
  methodName: string,
  expressionText: string,
  scanProfile: ScanProfile
): boolean {
  if (scanProfile === "test_fixture") {
    return false;
  }
  if (methodName !== "storageState") {
    return true;
  }
  if (scanProfile === "session_lifecycle") {
    return !(
      TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS.has(expressionText) ||
      TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS.has(lastPathPart(expressionText))
    );
  }
  if (scanProfile === "provider_action") {
    return (
      TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS.has(expressionText) ||
      TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS.has(lastPathPart(expressionText))
    );
  }
  return false;
}

function lastPathPart(expressionText: string): string {
  const parts = expressionText.split(".");
  return parts[parts.length - 1] ?? expressionText;
}

function scanProfileForFile(fileName: string): ScanProfile {
  const normalized = fileName.replaceAll("\\", "/");
  if (normalized.endsWith(".test.ts") || normalized.includes("/tests/")) {
    return "test_fixture";
  }
  if (
    normalized.endsWith("loginRelay.ts") ||
    normalized.endsWith("sessionStore.ts")
  ) {
    return "session_lifecycle";
  }
  return "provider_action";
}

function loadBoundaryRegistry(): BoundaryRegistry {
  const registryUrl = new URL("../../../src/seektalent/providers/pi_agent/boundary_registry.json", import.meta.url);
  return JSON.parse(readFileSync(registryUrl, "utf8")) as BoundaryRegistry;
}

if (import.meta.main) {
  const exitCode = await main(Bun.argv.slice(2));
  process.exit(exitCode);
}
