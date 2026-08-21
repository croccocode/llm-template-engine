/* tslint:disable */
/* eslint-disable */
type UndefinedBehavior = "strict" | "chainable" | "lenient" | "semi_strct";
/**
 * Represents a MiniJinja environment.
 */
export class Environment {
  free(): void;
  /**
   * Registers a new template by name and source.
   */
  addTemplate(name: string, source: string): void;
  /**
   * Removes a global again.
   */
  removeGlobal(name: string): void;
  /**
   * Clears all templates from the environment.
   */
  clearTemplates(): void;
  /**
   * Enables python compatibility.
   */
  enablePyCompat(): void;
  /**
   * Removes a template by name.
   */
  removeTemplate(name: string): void;
  /**
   * Like `renderStr` but with a named template for auto escape detection.
   */
  renderNamedStr(name: string, source: string, ctx: any): string;
  /**
   * Renders a registered template by name with the given context.
   */
  renderTemplate(name: string, ctx: any): string;
  /**
   * Sets a callback to join template paths (for relative includes/extends).
   *
   * The callback receives `(name, parent)` and should return a joined path string.
   * If it throws or returns a non-string, the original `name` is used.
   */
  setPathJoinCallback(func: Function): void;
  constructor();
  /**
   * Registers a test function.
   */
  addTest(name: string, func: Function): void;
  /**
   * Evaluates an expression with the given context.
   *
   * This is useful for evaluating expressions outside of templates.  The expression is
   * parsed and evaluated immediately.
   */
  evalExpr(expr: string, ctx: any): any;
  /**
   * Registers a filter function.
   */
  addFilter(name: string, func: Function): void;
  /**
   * Registers a value as global.
   */
  addGlobal(name: string, value: any): void;
  /**
   * Renders a string template with the given context.
   *
   * This is useful for one-off template rendering without registering the template.  The
   * template is parsed and rendered immediately.
   */
  renderStr(source: string, ctx: any): string;
  /**
   * Registers a synchronous template loader callback.
   *
   * The provided function is called with a template name and must return a
   * string with the template source or `null`/`undefined` if the template
   * does not exist. Errors thrown are propagated as MiniJinja errors.
   */
  setLoader(func: Function): void;
  /**
   * Enables or disables block trimming.
   */
  trimBlocks: boolean;
  /**
   * Enables or disables the lstrip blocks feature.
   */
  lstripBlocks: boolean;
  /**
   * Reconfigures the behavior of undefined variables.
   */
  undefinedBehavior: UndefinedBehavior;
  /**
   * Enables or disables keeping of the final newline.
   */
  keepTrailingNewline: boolean;
  /**
   * Configures the max-fuel for template evaluation.
   */
  get fuel(): number | undefined;
  set fuel(value: number | null | undefined);
  /**
   * Enables or disables debug mode.
   */
  debug: boolean;
}
