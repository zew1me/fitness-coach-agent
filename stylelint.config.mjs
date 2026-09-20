/** @type {import('stylelint').Config} */
const config = {
  extends: ["stylelint-config-standard", "stylelint-config-css-modules"],
  rules: {
    // CSS Module exports are consumed as camelCase properties throughout the TypeScript code.
    "selector-class-pattern": null,
    // Existing styles use broadly supported legacy color syntax; modernization is not a correctness concern.
    "alpha-value-notation": null,
    "color-function-alias-notation": null,
    "color-function-notation": null,
    "color-hex-length": null,
    // Existing media queries retain the widely understood min/max syntax for readability.
    "media-feature-range-notation": null,
    // Component styles intentionally group overrides near their UI states, which can invert specificity order.
    "no-descending-specificity": null,
    // CSS Modules composes values are case-sensitive local export names, not CSS keywords.
    "value-keyword-case": ["lower", { ignoreProperties: ["composes"] }],
  },
};

export default config;
