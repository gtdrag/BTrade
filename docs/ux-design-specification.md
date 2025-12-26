---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
inputDocuments:
  - docs/analysis/brainstorming-session-2025-12-25.md
workflowType: 'ux-design'
lastStep: 14
status: complete
project_name: 'georgedrag.ai'
user_name: 'George'
date: '2025-12-25'
completedAt: '2025-12-25'
---

# UX Design Specification - georgedrag.ai

**Author:** George
**Date:** 2025-12-25

---

## Executive Summary

### Project Vision

georgedrag.ai is a living, editorial-style portfolio where the site itself serves as the bio. Rather than telling visitors who George is, the site shows them through curated self-expression: personal photography as the visual canvas, coding projects for technical substance, and music for creative range. The design language—late 60s/70s editorial, confident typography, subtle physics—communicates taste and standards without a single word of self-description.

**Core Concept:** Art wrapping substance. A gallery aesthetic with dev portfolio function.

### Target Users

| Audience | What They Get |
|----------|---------------|
| **Hiring Managers** | Proof of taste, quality, and ability to ship |
| **Fellow Developers** | Technical substance — commits, code, versions |
| **Curious Visitors** | An experience that makes them want to know more |

*Note: The site is not designed FOR these audiences. It's designed to authentically express George. The right people will resonate.*

### Key Design Challenges

1. **The Entrance** — Bold hyperlink that feels inviting, not cold
2. **Photography as UI** — Images as both content and structure
3. **Dual Audience** — Technical depth for devs, aesthetic experience for all
4. **Dynamic Integration** — Apple Photos, GitHub, Bandcamp with graceful fallbacks
5. **Page Structure** — Single scroll vs individual pages (needs prototyping)

### Design Opportunities

1. **Hyperlink Entrance** — Subverts portfolio conventions, creates intrigue
2. **Photography-First** — Instant differentiation from typical dev portfolios
3. **Implicit Bio** — Curation as communication, showing not telling
4. **Living Content** — Site evolves as George's work evolves

---

## Core User Experience

### Defining Experience

The core experience is **discovery through visual immersion**. Visitors encounter George's work not through explicit navigation or instruction, but through an editorial presentation that invites exploration. The exact interaction pattern (scroll vs click-through) will be determined through prototyping.

**Core Principle:** Let the visitor absorb and decide if they vibe. No hand-holding.

### Platform Strategy

- **Web-first** at georgedrag.ai
- **Responsive** — works on mobile, optimized for desktop
- **No offline requirements**
- **Mouse/trackpad primary**, touch-friendly

### Effortless Interactions

- **Entrance is obvious** — hyperlink conventions (hover state, cursor change)
- **No instructions needed** — trust web literacy
- **Content loads gracefully** — photos, GitHub data, Bandcamp embeds
- **Navigation is implicit** — scroll or click, whichever feels right

### Critical Success Moments

1. **The click** — Visitor clicks the name, enters the experience
2. **The first impression** — Photography + typography hits immediately
3. **The "I get it"** — Visitor understands this is someone with taste
4. **The depth** — If curious, they find real substance (commits, music)

### Experience Principles

1. **Show, don't tell** — No bio paragraphs, no "About Me"
2. **Trust the visitor** — They know how the web works
3. **Reward curiosity** — Depth is there for those who look
4. **Feel > Function** — It should feel like art first

---

## Desired Emotional Response

### Primary Emotional Goal

**"Art."** The site should feel like encountering a piece of art — not a portfolio template.

### Emotional Journey Mapping

| Moment | Desired Feeling |
|--------|-----------------|
| **Landing** | Intrigued — "What is this?" |
| **The Click** | Curiosity rewarded — something opens up |
| **First Impression** | Recognition — "This person has taste" |
| **Exploring Projects** | Respect — "They ship real stuff" |
| **Overall** | Understanding — "I get who this person is without being told" |

### Emotions to Avoid

- **Confused** — "Where do I click?"
- **Bored** — "Another developer portfolio..."
- **Overwhelmed** — "Too much going on"
- **Cold/Distant** — "This feels sterile"

### Emotional Design Principles

1. **Intrigue over explanation** — Let them wonder, then discover
2. **Confidence over instruction** — Trust they know how to navigate
3. **Warmth within minimalism** — Simple but not cold
4. **Substance behind beauty** — Art on the surface, depth underneath

---

## UX Pattern Analysis & Inspiration

### Inspiring Products Analysis

**Design Era:** Late 60s/early 70s graphic design, Swiss International Style

**Key Designers:**
- **Massimo Vignelli** — Grid systems, restraint, timeless typography
- **Paul Rand** — Bold simplicity, logos as art
- **Saul Bass** — Confident asymmetry, iconic visual language

**Editorial Reference:** Richard Avedon / Harper's Bazaar — full-bleed photography, confident typography floating over images

### Transferable UX Patterns

| From | Pattern | Apply To Your Site |
|------|---------|-------------------|
| Vignelli | Grid systems, restraint | Layout structure, spacing |
| Avedon | Image IS the design | Full-bleed photos as canvas |
| Swiss Style | Bold typography hierarchy | Name as entrance, section headers |
| Classic Editorial | Confident asymmetry | Text floating on images |
| Harper's Bazaar | White space as luxury | Generous margins, breathing room |

### Anti-Patterns to Avoid

- Dark mode terminal aesthetic (typical dev portfolio)
- Hamburger menus everywhere
- Parallax overload
- Stock photography
- Busy navigation bars
- Cookie-cutter portfolio templates
- Sterile minimalism (cold, lifeless)

---

## Design System Foundation

### Design System Choice

**Custom Design System with Tailwind CSS Utilities**

A fully custom design system built on Tailwind CSS utilities, enabling complete visual control while leveraging proven utility-first patterns.

### Rationale for Selection

1. **Visual Uniqueness Required** — Late 60s/70s editorial aesthetic doesn't exist in any established component library
2. **Brand Expression** — The site IS the bio; every visual choice communicates identity
3. **Performance** — Tailwind's purge system keeps bundle size minimal
4. **Developer Experience** — Rapid prototyping with utility classes, custom components as needed

### Implementation Approach

- **Foundation:** Tailwind CSS for utilities, spacing, responsive
- **Typography:** Custom font stack (Helvetica Neue or equivalent)
- **Motion:** Framer Motion for subtle physics and weight
- **Components:** Custom React/Next.js components for photography, projects, music

### Customization Strategy

| Layer | Approach |
|-------|----------|
| **Spacing** | Generous Swiss-style scale (large margins, breathing room) |
| **Colors** | Restrained palette: 2-3 colors max + photography as color |
| **Typography** | Bold, confident hierarchy — type as design element |
| **Motion** | Subtle, weighted, never gratuitous |
| **Components** | Built from scratch for editorial feel |

---

## Defining Core Experience

### The Defining Interaction

**"Click George's name. Enter art."**

The moment between clicking the hyperlink entrance and the first impression of full-bleed photography with floating typography — this is the interaction that makes visitors understand who George is without being told.

### User Mental Model

| What Users Expect | What They Get |
|-------------------|---------------|
| Typical portfolio | Art experience |
| Navigation bar | Implicit exploration |
| "About Me" section | Curation as communication |
| Dark mode terminal | Late 60s/70s editorial |

**Key Insight:** Subvert expectations with the reveal, but use familiar patterns for entry (hyperlink) and exploration (scroll/click).

### Success Criteria

1. **Entrance is obvious** — Hyperlink conventions respected (hover, cursor)
2. **First impression hits fast** — Photography + typography within 1 second
3. **Taste is communicated** — "This person has standards"
4. **Substance is discoverable** — Projects show real commits, versions, live links

### Experience Mechanics

| Phase | Mechanic |
|-------|----------|
| **Initiation** | Name as hyperlink, hover reveals clickability |
| **Transition** | Click opens into full-bleed photography |
| **First Impression** | Art hits immediately — no loading states, no transitions that delay |
| **Exploration** | Scroll or click, visitor chooses pace |
| **Depth** | GitHub commits, Bandcamp music, live project links |

---

## Visual Design Foundation

### Color System

**Philosophy:** Photography IS the color. The restrained UI palette lets images shine.

| Role | Value | Usage |
|------|-------|-------|
| **Primary Text** | Near-black (#1a1a1a) | Headings, body |
| **Secondary Text** | Medium gray (#666) | Captions, metadata |
| **Background** | Off-white (#fafafa) | Non-photo areas |
| **Overlay Text** | White (#fff) | Text floating on photos |
| **Accent** | TBD via prototyping | Links, interactions |

### Typography System

**Typeface:** Helvetica Neue (or Inter as web equivalent)

| Level | Size | Weight | Usage |
|-------|------|--------|-------|
| **Display** | 72-96px | Light/Regular | Name entrance |
| **H1** | 48-64px | Regular | Section headers |
| **H2** | 32-40px | Regular | Project titles |
| **Body** | 18-20px | Regular | Content |
| **Caption** | 14-16px | Regular | Metadata |

**Principles:** Confident sizing, generous line-height (1.5-1.6), type as design element.

### Spacing & Layout Foundation

| Principle | Value |
|-----------|-------|
| **Base Unit** | 8px |
| **Section Padding** | 80-120px |
| **Component Gaps** | 24-48px |
| **Margins** | 10-15% viewport (desktop) |
| **Grid** | Fluid, asymmetric, Swiss-inspired |

### Design Philosophy Note

Design purity takes priority. No visual compromises for accessibility compliance — the aesthetic is the point. AI-assisted browsing and screen readers are evolving rapidly; the site is optimized for its intended audience.

---

## Design Direction

### Chosen Direction

**Editorial Art Portfolio** — Richard Avedon meets Massimo Vignelli

A single, unified design direction derived from brainstorming. No variations needed — the vision is clear.

### Direction Summary

| Element | Approach |
|---------|----------|
| **Layout** | Full-bleed photography with floating typography |
| **Hierarchy** | Name entrance → Photo reveal → Content sections |
| **Density** | Generous Swiss-style spacing |
| **Navigation** | Implicit (scroll/click), no visible nav |
| **Motion** | Subtle physics, weighted, tactile |
| **Typography** | Confident, bold, type as design element |

### Design Rationale

1. **Locked in from brainstorming** — Vision was clear from values archaeology
2. **Subverts expectations** — Nothing like typical dark-mode dev portfolios
3. **Art wrapping substance** — Gallery aesthetic with dev portfolio function
4. **The site IS the bio** — Curation communicates identity

### To Be Validated in Prototyping

- Single scroll vs. click-through page structure
- Photo curation from Apple Photos album
- Exact type scale for display/entrance
- Motion timing and physics feel

---

## User Journey Flows

### Journey 1: First Impression (All Visitors)

Entry → Click name → Full-bleed photography reveal → "Art" feeling → Optional exploration

**Success Metric:** Visitor leaves with "this person has taste" impression.

### Journey 2: Hiring Manager

First impression → Projects section → Live links → "They ship quality" impression

**Success Metric:** Proof of taste, quality, and ability to ship.

### Journey 3: Fellow Developer

First impression → Projects → GitHub integration (commits, versions) → Technical depth

**Success Metric:** Confirms real technical substance behind the aesthetic.

### Journey Patterns

| Pattern | Application |
|---------|-------------|
| **Single Entry** | Everyone enters through name hyperlink |
| **Progressive Depth** | Art → Content → Technical (self-selected) |
| **Self-Selection** | Visitors choose their own depth |
| **No Dead Ends** | Every section connects to the flow |

### Flow Optimization Principles

1. **Immediate Payoff** — Art hits within 1 second
2. **Zero Friction** — No interruptions, modals, or sign-ups
3. **Trust the Visitor** — They know how the web works
4. **Depth is Optional** — Impression works without exploration

---

## Component Strategy

### Foundation Layer

Tailwind CSS provides utility classes; all UI components are custom-built.

### Custom Components

| Component | Purpose | Priority |
|-----------|---------|----------|
| **Entrance** | Name hyperlink gateway | Phase 1 |
| **PhotoCanvas** | Full-bleed photography + floating text | Phase 1 |
| **ProjectCard** | GitHub-integrated project display | Phase 2 |
| **MusicEmbed** | Bandcamp with editorial styling | Phase 3 |
| **SectionContainer** | Swiss-style spacing wrapper | Phase 2 |

### Component Design Principles

1. **Editorial First** — Every component serves the late 60s/70s aesthetic
2. **Subtle Physics** — Hover states and transitions feel weighted
3. **Typography as Hero** — Text is a design element, not just content
4. **Photography Integration** — Components work with full-bleed images

### Implementation Approach

- Build with React/Next.js
- Style with Tailwind utilities
- Animate with Framer Motion
- Integrate GitHub API for project data
- Embed Bandcamp players for music

---

## UX Consistency Patterns

### Link Patterns

| Link Type | Style | Hover | Behavior |
|-----------|-------|-------|----------|
| **Entrance** | Display typography | Underline/color shift | Opens PhotoCanvas |
| **Internal** | Body typography | Subtle underline | Scrolls or navigates |
| **External** | Body + subtle indicator | Underline | New tab |

### Interaction Patterns

| Element | Interaction |
|---------|-------------|
| **Hover timing** | 200-300ms ease-out |
| **Physics feel** | Weighted, confident, not bouncy |
| **Cursor** | Pointer on all interactive elements |

### Loading Patterns

- Photos: Graceful fade-in, no skeletons
- GitHub data: Show cached, fallback to static
- Errors: Fail gracefully, no modals or alerts

### Anti-Patterns

- No modals or overlays
- No toast notifications
- No loading spinners
- No form validation (no forms)

---

## Responsive Design

### Strategy

Desktop-first design. The editorial aesthetic is optimized for large screens; mobile adapts but doesn't compromise the vision.

### Breakpoints

| Breakpoint | Target | Key Adaptations |
|------------|--------|-----------------|
| **1280px+** | Desktop | Full margins, full typography scale |
| **1024px** | Small desktop | Slightly reduced margins |
| **768px** | Tablet | Single column, scaled type |
| **< 768px** | Mobile | Stacked layout, minimal margins |

### Device Behavior

- **Photography:** Full-bleed at all sizes
- **Typography:** Scales proportionally (72px → 48px → 32px for display)
- **Margins:** 10-15% desktop → 5-8% tablet → 16-24px mobile
- **Layout:** Asymmetric desktop → Single column mobile

### Testing

- Real device testing (iPhone, iPad, MacBook)
- Safari and Chrome primary
- Photo loading performance on mobile networks

