// The Daily Cronicle — Kindle Scribe edition. Saturdays render the Weekend Brief
// (data.edition == "weekend"): the week's threads instead of the day's stories.
// Page is 6.1 x 8.2in: the Scribe's 10.2in 4:3 panel at 300ppi.
// Single column — two columns at this width leaves ~28 characters a line.

#let data = json("data.json")

#set document(title: data.title, author: "scribe-daily")
#set page(
  width: 6.1in,
  height: 8.2in,
  // Bottom margin carries an extra 35pt of white space under the footer
  // (owner 2026-09-28: +15pt; 2026-09-30: +20pt more, the footer rule still
  // sat against the bottom of the screen).
  margin: (x: 0.45in, top: 0.5in, bottom: 0.55in + 35pt),
  footer: context [
    #set text(size: 7.5pt, fill: luma(80))
    #line(length: 100%, stroke: 0.3pt + luma(160))
    #v(2pt)
    #grid(columns: (1fr, auto, 1fr),
      align(left)[#data.date_long],
      align(center)[#counter(page).display()],
      align(right)[#link(<contents>)[#data.short_name]])
  ],
)

// Libertinus ships with Typst, so no font installation in CI.
#set text(font: "Libertinus Serif", size: 10.5pt, hyphenate: true)
#set par(justify: true, leading: 0.62em)
#set heading(numbering: none)
#show heading.where(level: 1): it => block(width: 100%)[
  #v(4pt)
  #set text(size: 15pt, weight: "bold", tracking: 0.4pt)
  #upper(it.body)
  #v(1pt)
  #line(length: 100%, stroke: 1.2pt)
  #v(6pt)
]
#show heading.where(level: 2): it => block[
  #set text(size: 12.5pt, weight: "bold")
  #it.body
  #v(2pt)
]
#show link: set text(fill: black)

// Dynamic labels: metadata is invisible and a label binds to the element
// immediately before it, which gives us a queryable anchor per article, per
// group, and per section page.
#let anchor(id) = [#metadata(id)#label(id)]

// Page reference that degrades gracefully if the target was never emitted.
#let pageref(id) = context {
  let hits = query(label(id))
  if hits.len() > 0 {
    text(size: 9pt, fill: luma(70))[p.#counter(page).at(hits.first().location()).first()]
  }
}

#let jump(id, body) = context {
  if query(label(id)).len() > 0 { link(label(id), body) } else { body }
}

#let home() = align(center)[
  #v(4pt)
  #text(size: 8.5pt, fill: luma(70))[#link(<frontpage>)[← Front page]]
]

// A contents line: title on the left, page number on the right, both linked
// when the target exists (jump/pageref both no-op gracefully otherwise).
// Rows are spaced for a fingertip, not a mouse: ~0.35in pitch on the Scribe.
#let contents_line(id, title) = block(below: 13pt)[
  #grid(columns: (1fr, auto), gutter: 4pt,
    jump(id, text(size: 11pt)[#title]),
    jump(id, pageref(id)))
]

// ─────────────────────────────────────────────── Front page
#align(center)[
  #text(size: 9pt, tracking: 2pt)[#upper(data.date_long)]
  #v(3pt)
  #line(length: 100%, stroke: 1.6pt)
  #v(3pt)
  // The Daily Cronicle wordmark (owner, 2026-09-30), copied into build/ by
  // render.compile_pdf. Used on Saturdays too; the week line below marks
  // the Weekend Brief.
  #image("masthead.png", width: 100%, alt: data.masthead)
  #v(2pt)
  #line(length: 100%, stroke: 0.6pt)
  #if data.edition == "weekend" [
    #v(2pt)
    #text(size: 9pt, style: "italic")[The week of #data.week_range]
  ]
]
#metadata("frontpage")#label("frontpage")
#v(10pt)

#block(inset: (x: 6pt), fill: luma(245), width: 100%)[
  #pad(8pt)[
    #if data.mode == "automatic" [
      #text(size: 8pt, tracking: 1pt, fill: luma(90))[#upper[Automatic edition]]
      #v(3pt)
    ]
    #set par(justify: true, first-line-indent: 0em)
    #set text(size: 10.5pt)
    #data.brief
  ]
]

// The brief keeps the front page to itself (owner, 2026-09-28: keep the
// longer brief, move the contents rather than shorten it).
#pagebreak()
#heading(level: 1)[#if data.edition == "weekend" [This Week] else [Today]]
#metadata("contents")#label("contents")
#if data.edition == "weekend" and data.mode == "edited" [
  #for (i, t) in data.threads.enumerate() [
    #contents_line("thread" + str(i), t.title)
  ]
  #if data.world != none [
    #contents_line("worldpage", "The World This Week")
  ]
  #if data.also.len() > 0 [
    #contents_line("alsopage", "Also This Week")
  ]
] else if data.mode == "edited" [
  #for (i, g) in data.groups.enumerate() [
    #contents_line("group" + str(i), g.title)
  ]
] else [
  #if data.week.len() > 0 [
    #contents_line("weekpage", "The Week as It Ran")
  ]
  #contents_line("headlinespage", "Headlines")
]
#if data.radar.len() > 0 [
  #contents_line("radarpage", "On Your Radar")
]
#if data.chatter.len() > 0 [
  #contents_line("chatterpage", "The Chatter")
]
#if data.full_text.len() > 0 [
  #contents_line("fulltextpage", "Read in Full")
]

// ─────────────────────────────────────────────── The Day's Stories (edited)
// Groups flow continuously — no pagebreak per group, pages are precious —
// but a heading is never left orphaned at a page bottom: block(breakable:
// false) around the heading, its anchor and its first paragraph pins them
// together onto one page.
#if data.edition == "daily" and data.mode == "edited" [
  #pagebreak()
  #heading(level: 1)[The Day's Stories]
  #for (i, g) in data.groups.enumerate() [
    #let gid = "group" + str(i)
    #block(breakable: false)[
      #heading(level: 2)[#g.title]
      #anchor(gid)
      #if g.paragraphs.len() > 0 [
        #set par(justify: true, first-line-indent: 0em)
        #g.paragraphs.first()
      ]
    ]
    #set par(justify: true, first-line-indent: 0em)
    #for p in g.paragraphs.slice(1) [
      #parbreak()
      #p
    ]
    #v(6pt)
    // Five body lines (10.5pt text + 0.62em leading) of space before the
    // next story's headline (owner, 2026-09-30).
    #block(below: 5 * (10.5pt + 0.62 * 10.5pt))[
      #set text(size: 8.5pt, fill: luma(80))
      // Source names and headlines open the original in the browser (owner,
      // 2026-09-28); "Read in full p.N" stays an in-document jump.
      Sources: #g.sources.map(s => link(s.url)[#s.name #sym.arrow.tr]).join(" · ")
      #v(4pt)
      #for s in g.stories [
        #block(below: 6pt)[
          #set par(justify: false, hanging-indent: 10pt)
          • #link(s.url)[#s.title #sym.arrow.tr]
          #h(3pt) #text(fill: luma(90), style: "italic")[#s.source_name]
          #if s.full_text [
            #h(3pt) #jump(s.id)[#text(fill: luma(60))[Read in full] #h(2pt) #pageref(s.id)]
          ]
        ]
      ]
    ]
  ]
]

// ─────────────────────────────────────────────── The Week's Stories (weekend)
// One thread per story that developed over the week. The heading, status
// line and timeline are pinned together with the first paragraph, as with the
// daily groups, so a thread never opens at the foot of a page.
#if data.edition == "weekend" and data.mode == "edited" [
  #pagebreak()
  #heading(level: 1)[The Week's Stories]
  #for (i, t) in data.threads.enumerate() [
    #let tid = "thread" + str(i)
    #block(breakable: false)[
      #heading(level: 2)[#t.title]
      #anchor(tid)
      #text(size: 8pt, tracking: 0.8pt, fill: luma(80))[
        #upper(t.status) · covered on #t.days #if t.days == 1 [day] else [days]
      ]
      #if t.timeline.len() > 0 [
        #v(4pt)
        #block(inset: (left: 8pt, y: 2pt), stroke: (left: 0.8pt + luma(120)))[
          #set text(size: 9pt)
          #set par(justify: false)
          #grid(columns: (auto, 1fr), column-gutter: 8pt, row-gutter: 5pt,
            ..t.timeline.map(e => (text(weight: "bold")[#e.day], e.line)).flatten())
        ]
      ]
      #v(6pt)
      #if t.paragraphs.len() > 0 [
        #set par(justify: true, first-line-indent: 0em)
        #t.paragraphs.first()
      ]
    ]
    #set par(justify: true, first-line-indent: 0em)
    #for p in t.paragraphs.slice(1) [
      #parbreak()
      #p
    ]
    #v(6pt)
    // Kept whole: a lone "Read in full" line stranded on the next page
    // reads as a stray. It's ~8 short lines at most. Five body lines of
    // space follow it, before the next thread's headline, as in the daily.
    #block(breakable: false, below: 5 * (10.5pt + 0.62 * 10.5pt))[
      #set text(size: 8.5pt, fill: luma(80))
      #set par(justify: false, hanging-indent: 10pt)
      Coverage:
      #v(3pt)
      #for c in t.coverage [
        #block(below: 4pt)[
          #text(weight: "bold")[#c.day] #h(3pt)
          #c.sources.map(s => link(s.url)[#s.name #sym.arrow.tr]).join(" · ")
        ]
      ]
      #for r in t.reads [
        #block(below: 4pt)[
          • #jump(r.id)[#r.title] #h(3pt) #jump(r.id)[#text(fill: luma(60))[Read in full] #h(2pt) #pageref(r.id)]
        ]
      ]
    ]
  ]

  // One synthesis of the week's world news and global politics (owner,
  // 2026-09-29). Laid out like a thread, without the status and timeline.
  #if data.world != none [
    #let w = data.world
    #pagebreak()
    #block(breakable: false)[
      #heading(level: 1)[The World This Week]
      #metadata("worldpage")#label("worldpage")
      #if w.paragraphs.len() > 0 [
        #set par(justify: true, first-line-indent: 0em)
        #w.paragraphs.first()
      ]
    ]
    #set par(justify: true, first-line-indent: 0em)
    #for p in w.paragraphs.slice(1) [
      #parbreak()
      #p
    ]
    #v(6pt)
    #block(breakable: false, below: 16pt)[
      #set text(size: 8.5pt, fill: luma(80))
      #set par(justify: false, hanging-indent: 10pt)
      Coverage:
      #v(3pt)
      #for c in w.coverage [
        #block(below: 4pt)[
          #text(weight: "bold")[#c.day] #h(3pt)
          #c.sources.map(s => link(s.url)[#s.name #sym.arrow.tr]).join(" · ")
        ]
      ]
      #for r in w.reads [
        #block(below: 4pt)[
          • #jump(r.id)[#r.title] #h(3pt) #jump(r.id)[#text(fill: luma(60))[Read in full] #h(2pt) #pageref(r.id)]
        ]
      ]
    ]
  ]

  #if data.also.len() > 0 [
    #pagebreak()
    #heading(level: 1)[Also This Week]
    #metadata("alsopage")#label("alsopage")
    #for x in data.also [
      #block(below: 8pt)[
        #set par(justify: false, hanging-indent: 10pt)
        #text(size: 8pt, tracking: 0.6pt, fill: luma(80))[#upper(x.day)] #h(3pt)
        #text(size: 10pt, weight: "bold")[#if x.url != none [#link(x.url)[#x.title]] else [#x.title]]
        #if x.line != "" [
          #linebreak()
          #text(size: 9.5pt, fill: luma(50))[#x.line]
        ]
      ]
    ]
  ]
]

// ─────────────────────────────────────────────── The Week as It Ran (automatic weekend)
#if data.week.len() > 0 [
  #pagebreak()
  #heading(level: 1)[The Week as It Ran]
  #metadata("weekpage")#label("weekpage")
  #for d in data.week [
    #block(breakable: false)[#heading(level: 2)[#d.day_long]]
    #if d.brief != "" [
      #set par(justify: true, first-line-indent: 0em)
      #d.brief
      #v(4pt)
    ]
    #for item in d.items [
      #block(below: 5pt)[
        #set par(justify: false, hanging-indent: 10pt)
        #text(size: 9.5pt)[• #item]
      ]
    ]
    #v(8pt)
  ]
]

// ─────────────────────────────────────────────── Headlines (automatic)
#if data.mode == "automatic" [
  #pagebreak()
  #heading(level: 1)[Headlines]
  #metadata("headlinespage")#label("headlinespage")
  #for src in data.headlines [
    #heading(level: 2)[#src.source_name]
    #for item in src.items [
      #block(below: 10pt)[
        #set par(justify: true, first-line-indent: 0em)
        #jump(item.id, text(size: 11pt, weight: "bold")[#item.title])
        #if item.full_text [
          #h(4pt) #text(size: 8.5pt, fill: luma(70))[Read in full] #h(2pt) #pageref(item.id)
        ]
        #if item.digest != "" [
          #linebreak()
          #text(size: 10pt)[#item.digest]
        ]
      ]
    ]
  ]
]

// ─────────────────────────────────────────────── On Your Radar
#if data.radar.len() > 0 [
  #pagebreak()
  #heading(level: 1)[On Your Radar]
  #metadata("radarpage")#label("radarpage")
  #for item in data.radar [
    #block(below: 7pt)[
      #set par(justify: false, hanging-indent: 10pt)
      #text(size: 10pt)[
        #jump(item.id, text(weight: "bold")[#item.title])
        #h(3pt) #text(size: 8.5pt, fill: luma(80), style: "italic")[#item.source_name]
        #h(3pt) #pageref(item.id)
      ]
      #if item.line != "" [
        #linebreak()
        #text(size: 9.5pt, fill: luma(50))[#item.line]
      ]
    ]
  ]
]

// ─────────────────────────────────────────────── The Chatter
#if data.chatter.len() > 0 [
  #pagebreak()
  #heading(level: 1)[The Chatter]
  #metadata("chatterpage")#label("chatterpage")
  #for item in data.chatter [
    #block(below: 7pt)[
      #set par(justify: false, hanging-indent: 10pt)
      #text(size: 10pt)[
        #jump(item.id, text(weight: "bold")[#item.title])
        #h(3pt) #text(size: 8.5pt, fill: luma(80), style: "italic")[#item.source_name]
        #h(3pt) #pageref(item.id)
      ]
      #if item.line != "" [
        #linebreak()
        #text(size: 9.5pt, fill: luma(50))[#item.line]
      ]
    ]
  ]
]

// ─────────────────────────────────────────────── Read in Full
// A level-1 heading here, not just the per-article level-2 ones below, is
// load-bearing: Typst's PDF outline nests headings purely by document order,
// not by topical relation, so without a level-1 heading of their own every
// full-text article would nest under whichever heading happened to come
// last in the Scribe's Contents panel. The heading shares a page with the
// first article rather than getting its own pagebreak, so there's no blank
// divider page.
#if data.full_text.len() > 0 [
  #pagebreak()
  #heading(level: 1)[Read in Full]
  #metadata("fulltextpage")#label("fulltextpage")
]
#for (i, art) in data.full_text.enumerate() [
  #if i > 0 [#pagebreak()]
  #heading(level: 2)[#art.title]
  #anchor(art.id)
  #text(size: 8.5pt, fill: luma(80))[
    #art.source_name
    #if art.word_count > 0 [ · #art.word_count words]
  ]
  #v(6pt)

  #if art.local_image != none [
    #image(art.local_image, width: 100%)
    #v(6pt)
  ]

  #set par(justify: true, first-line-indent: 1em)
  #for para in art.paragraphs [
    #para
    #parbreak()
  ]

  #v(8pt)
  #line(length: 30%, stroke: 0.4pt + luma(160))
  #v(4pt)
  #grid(columns: (auto, 1fr), gutter: 8pt,
    image(art.qr_path, width: 0.62in),
    [
      #set text(size: 8pt, fill: luma(80))
      #v(4pt)
      Scan to open the original on your phone.
    ])
  #home()
]

// ─────────────────────────────────────────────── Colophon
#pagebreak()
#align(center + horizon)[
  #set text(size: 8.5pt, fill: luma(90))
  #set par(justify: false)
  #box(width: 70%)[#data.colophon]
]
