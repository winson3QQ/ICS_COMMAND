export default [
  {
    ignores: [
      'static/lib/**/*.js'
    ],
  },
  {
    files: ['static/js/**/*.js', 'tests/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        AbortSignal: 'readonly',
        Blob: 'readonly',
        Chart: 'readonly',
        CustomEvent: 'readonly',
        L: 'readonly',
        TextEncoder: 'readonly',
        URL: 'readonly',
        alert: 'readonly',
        clearTimeout: 'readonly',
        console: 'readonly',
        confirm: 'readonly',
        crypto: 'readonly',
        devicePixelRatio: 'readonly',
        document: 'readonly',
        fetch: 'readonly',
        location: 'readonly',
        navigator: 'readonly',
        prompt: 'readonly',
        requestAnimationFrame: 'readonly',
        sessionStorage: 'readonly',
        setInterval: 'readonly',
        setTimeout: 'readonly',
        clearInterval: 'readonly',
        window: 'readonly'
      },
    },
    rules: {
      'no-dupe-else-if': 'error',
      'no-undef': 'error',
      'no-unreachable': 'error'
    },
  },
];
