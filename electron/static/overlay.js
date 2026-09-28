// Called by the main process through `executeJavaScript`. Resolves once the new
// captions have been laid out and painted, so the next frame can be captured.
//
// `options.watch` lays the texture out as a watch face: one rounded panel with
// the most recent captions, kept until newer ones replace them.
window.renderCaptions = (lines, labels, options = {}) => {
	const captions = lines.map((line) => {
		const caption = document.createElement('section')
		caption.className = `caption ${line.source}`
		const marker = document.createElement('div')
		marker.className = 'marker'
		const text = document.createElement('div')
		text.className = 'text'
		const primary = document.createElement('div')
		primary.className = 'primary'
		primary.textContent = line.primary
		text.append(primary)
		if (line.secondary || line.pending) {
			const secondary = document.createElement('div')
			secondary.className = line.secondary ? 'secondary' : 'secondary pending'
			secondary.textContent = line.secondary ?? labels.translating
			text.append(secondary)
		}
		caption.append(marker, text)
		return caption
	})

	let list = document.body
	document.body.classList.toggle('watch', Boolean(options.watch))
	if (options.watch) {
		list = document.createElement('div')
		list.className = 'face'
		if (captions.length === 0) {
			const empty = document.createElement('div')
			empty.className = 'empty'
			empty.textContent = labels.empty
			list.append(empty)
		}
		list.append(...captions)
		document.body.replaceChildren(list)
	} else {
		document.body.replaceChildren(...captions)
	}
	// Drop whole older captions rather than clipping one at the top edge.
	const top = list.getBoundingClientRect().top + parseFloat(getComputedStyle(list).paddingTop)
	while (list.childElementCount > 1 && list.firstElementChild.getBoundingClientRect().top < top)
		list.firstElementChild.remove()
	return new Promise((resolve) =>
		requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))
	)
}
