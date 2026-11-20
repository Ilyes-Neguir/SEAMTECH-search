import assert from "node:assert/strict"
import { test, beforeEach, afterEach } from "node:test"

import { secretInacceptable, uiPassword } from "./secrets.ts"

const ANCIEN = process.env.SEAMTECH_UI_PASSWORD

beforeEach(() => {
  delete process.env.SEAMTECH_UI_PASSWORD
})

afterEach(() => {
  if (ANCIEN === undefined) delete process.env.SEAMTECH_UI_PASSWORD
  else process.env.SEAMTECH_UI_PASSWORD = ANCIEN
})

test("secretInacceptable rejette les exemples et les secrets trop courts", () => {
  assert.equal(secretInacceptable("change-me"), true)
  assert.equal(secretInacceptable("short"), true)
  assert.equal(secretInacceptable("12345678"), false)
  assert.equal(secretInacceptable("123456789012345", 16), true)
  assert.equal(secretInacceptable("1234567890123456", 16), false)
})

test("uiPassword exige 16 caractères minimum", () => {
  process.env.SEAMTECH_UI_PASSWORD = "123456789012345"
  assert.equal(uiPassword(), "")
  process.env.SEAMTECH_UI_PASSWORD = "1234567890123456"
  assert.equal(uiPassword(), "1234567890123456")
})

test("uiPassword sans variable est non configuré", () => {
  assert.equal(uiPassword(), "")
})
