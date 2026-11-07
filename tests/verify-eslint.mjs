import assert from 'node:assert/strict';
import { ESLint } from 'eslint';

const directory = process.argv[2];
const extension = process.argv[3];
assert.ok(directory, 'A generated project directory is required');
assert.ok(extension, 'A JSX or TSX extension is required');
const eslint = new ESLint({
  cwd: directory,
  overrideConfigFile: `${directory}/eslint.config.mjs`,
});
const fixtures = [
  {
    name: 'valid',
    code: 'export function Greeting() { return <h1>Hello</h1>; }',
    rules: [],
  },
  {
    name: 'accessibility',
    code: 'export function Image() { return <img src="test.png" />; }',
    rules: ['jsx-a11y-x/alt-text'],
  },
  {
    name: 'hooks',
    code: 'import { useState } from "react"; export function Bad() { if (Math.random()) { useState(0); } return <h1>Hello</h1>; }',
    rules: ['react-hooks/rules-of-hooks'],
  },
  {
    name: 'keys',
    code: 'export function List() { return [1, 2].map(value => <span>{value}</span>); }',
    rules: ['@eslint-react/no-missing-key'],
  },
];
for (const fixture of fixtures) {
  const [result] = await eslint.lintText(fixture.code, {
    filePath: `src/Fixture.${extension}`,
  });
  assert.ok(result, `${fixture.name}: no lint result`);
  assert.equal(result.fatalErrorCount, 0, JSON.stringify(result.messages));
  if (!fixture.rules.length) {
    assert.equal(result.errorCount, 0, JSON.stringify(result.messages));
  }
  for (const rule of fixture.rules) {
    assert.ok(
      result.messages.some((message) => message.ruleId === rule && message.severity === 2),
      `${fixture.name}: missing blocking ${rule}: ${JSON.stringify(result.messages)}`,
    );
  }
}
console.log(`ESLint ${ESLint.version}: ${extension} baseline rules passed`);
