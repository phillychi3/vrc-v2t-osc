import assert from 'node:assert/strict'
import test from 'node:test'
import { CaptionBoard } from '../electron/src/main/overlay-captions.ts'

const show = { showMicrophone: true, showSpeaker: true }
let seq = 0
const event = (name, data) => ({
	v: 1,
	type: 'event',
	sessionId: 's',
	seq: ++seq,
	event: name,
	data
})
const transcript = (utteranceId, text, source) =>
	event('transcript.final', { utteranceId, text, source })

test('speaker captions show the translation first and the original below', () => {
	const board = new CaptionBoard()
	assert.ok(board.handle(transcript('a', 'Hello there', 'speaker'), true, 0))
	assert.deepEqual(board.lines(show), [
		{ id: 'a', source: 'speaker', primary: 'Hello there', secondary: null, pending: true }
	])
	board.handle(
		event('translation.result', { text: '你好', context: { utteranceId: 'a' } }),
		true,
		1
	)
	assert.deepEqual(board.lines(show)[0], {
		id: 'a',
		source: 'speaker',
		primary: '你好',
		secondary: 'Hello there',
		pending: false
	})
})

test('microphone captions keep the spoken text first', () => {
	const board = new CaptionBoard()
	board.handle(transcript('a', '你好', 'voice'), true, 0)
	board.handle(
		event('translation.result', { text: 'Hello', context: { utteranceId: 'a' } }),
		true,
		0
	)
	assert.equal(board.lines(show)[0].primary, '你好')
	assert.equal(board.lines(show)[0].secondary, 'Hello')
	assert.equal(board.lines(show)[0].source, 'microphone')
})

test('translation errors stop the pending state and keep the original', () => {
	const board = new CaptionBoard()
	board.handle(transcript('a', 'Hello', 'speaker'), true, 0)
	assert.ok(
		board.handle(
			event('translation.error', { message: 'x', context: { utteranceId: 'a' } }),
			true,
			0
		)
	)
	assert.deepEqual(board.lines(show)[0].pending, false)
	assert.equal(board.lines(show)[0].primary, 'Hello')
})

test('filters sources, keeps the newest captions and expires old ones', () => {
	const board = new CaptionBoard(2)
	board.handle(transcript('a', 'one', 'speaker'), false, 0)
	board.handle(transcript('b', 'two', 'voice'), false, 1_000)
	board.handle(transcript('c', 'three', 'speaker'), false, 2_000)
	board.handle(transcript('d', 'four', 'speaker'), false, 3_000)
	assert.deepEqual(
		board.lines({ showMicrophone: false, showSpeaker: true }).map((line) => line.id),
		['c', 'd']
	)
	assert.equal(board.nextExpiry(5_000, 3_000), 2_000)
	assert.ok(board.expire(5_000, 6_500))
	assert.deepEqual(
		board.lines(show).map((line) => line.id),
		['c', 'd']
	)
	board.expire(5_000, 9_000)
	assert.equal(board.nextExpiry(5_000, 9_000), null)
})

test('ignores unrelated or malformed events', () => {
	const board = new CaptionBoard()
	assert.equal(board.handle(event('osc.sent', {}), true), false)
	assert.equal(board.handle(event('transcript.final', { text: 1 }), true), false)
	assert.equal(board.handle(event('translation.result', { text: 'x', context: {} }), true), false)
})

test('placement preview ignores source filters and replaces itself', () => {
	const board = new CaptionBoard()
	board.preview(0)
	board.preview(1_000)
	const lines = board.lines({ showMicrophone: false, showSpeaker: false })
	assert.equal(lines.length, 1)
	assert.equal(lines[0].primary, '這是測試字幕，用來調整位置與大小')
	assert.equal(board.nextExpiry(5_000, 1_000), 5_000)
})

test('watch faces keep real captions and only expire the placement preview', () => {
	const board = new CaptionBoard()
	board.handle(transcript('a', 'kept', 'speaker'), false, 0)
	board.preview(0)
	assert.equal(board.nextExpiry(5_000, 1_000, true), 4_000)
	assert.ok(board.expire(5_000, 60_000, true))
	assert.deepEqual(
		board.lines(show).map((line) => line.id),
		['a']
	)
	assert.equal(board.nextExpiry(5_000, 60_000, true), null)
})
