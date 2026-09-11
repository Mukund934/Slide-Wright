/**
 * The words under the slide.
 *
 * The canvas draws what an audience sees. A deck also carries what the
 * presenter wrote, and on `nasa-bhutan-water` — a real deck in this project's
 * corpus — that is **2,708 words against 983 on the slides**. A reviewer who
 * read the canvas and closed the window had read 27% of what the deck says.
 *
 * Three decisions, each of them the restrained one:
 *
 *   · **Absent when there are none.** 110 of the 153 notes parts in the corpus
 *     hold nothing but a thumbnail placeholder and a slide number, so a strip
 *     that was always there would be empty most of the time and would teach
 *     people to ignore it.
 *   · **Bounded, and it scrolls.** A 244-word script is a real fixture here.
 *     Letting it push the canvas around would trade the thing being reviewed
 *     for the thing beside it.
 *   · **Marked only when it changed**, using the one attention colour, which
 *     means exactly that and nothing else. The canvas outlines changed shapes;
 *     notes belong to no shape, so without a mark of their own a rewritten
 *     script would sit under an unmarked slide and read as untouched.
 *
 * That last state is a **second line of defence today, not a live one**, and
 * saying so is the point: nothing in this engine writes a notes part, so two
 * versions of one session cannot differ here. The engine's comparison is live
 * — `slide-wright diff` over two decks reports it and exits 1 — and if a notes
 * delta ever reaches this surface it must not arrive silently. Deleting the
 * branch would make the interface correct only for as long as the applier
 * stays the way it is.
 */

interface SpeakerNotesProps {
  notes: string;
  /** True while comparing two versions whose notes differ on this slide. */
  changed?: boolean;
}

export function SpeakerNotes({ notes, changed = false }: SpeakerNotesProps) {
  if (!notes.trim()) return null;

  const lines = notes.split("\n").filter((line) => line.trim());

  return (
    <section
      aria-label="Speaker notes"
      data-notes-changed={changed || undefined}
      className={`max-h-28 shrink-0 overflow-y-auto border-t px-4 py-2 ${
        changed ? "border-changed bg-changed-wash" : "border-line"
      }`}
    >
      <h2
        className={`text-evidence mb-1 ${changed ? "text-changed" : "text-ink-faint"}`}
      >
        speaker notes{changed ? " · changed in this version" : ""}
      </h2>
      {lines.map((line, index) => (
        <p key={index} className="text-2xs leading-relaxed text-ink-muted">
          {line}
        </p>
      ))}
    </section>
  );
}
