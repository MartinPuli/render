# Lateral stands and pitch aprons

Use this reference when a stadium's long sides contain distinct platea tiers,
hospitality, media, technical areas, a player tunnel, seat-written identity, or
a visible paved border around the grass.

## Contents

1. Evidence model
2. Lateral stand construction
3. Real voids and access
4. Seat-written identity
5. Pitch apron construction
6. Validation
7. Animation and reporting

## 1. Evidence model

Build a per-side programme before adding detail. Record each item as mapped,
documented, reference-derived, or inferred.

| Field | Examples |
|---|---|
| side identity | local `+v`/`-v`, then verified stand name |
| tier stack | lower-inferior, lower, media, hospitality/boxes, upper |
| seating type | individual seats, standing terrace, press desks |
| field-level programme | tunnel, dugouts, officials, TV, circulation |
| access | vomitories, stairs, gates, accessible platforms |
| identity | coloured-seat words, flags, fascia, painted apron |
| apron | runoff, drain, curb, pavement, technical strip, end apron |

Do not transfer programme from one long side to the other without evidence.
One side may contain the player route and dugouts while the opposite side owns
the primary camera and press positions.

## 2. Lateral stand construction

Model every documented tier as a distinct editable band with its own inner and
outer depth, first/last-row height, row count, rake, and horizontal break. A
hospitality or media band is architecture, not a material stripe: include its
floor, soffit, frontage, access and guardrail.

For each long side:

1. Fit the inner lip to the pitch/runoff rectangle.
2. Fit the outer edge independently to the stadium envelope.
3. Construct row treads and risers per tier.
4. Add seat modules only to seated sectors.
5. Reserve actual aisle and portal openings before placing seats.
6. Add front rails, radial dividers and accessible platforms.
7. Integrate the side-specific operational programme.

Avoid one continuous dark rake. A real lateral must read as rows plus horizontal
tier breaks from the field, and as sectioned seating plus access from the air.

## 3. Real voids and access

Vomitories and tunnels are negative space. Remove or mask the affected tread and
seat modules, construct a recessed dark interior, then add a concrete frame,
landing and rails. A black cuboid placed on top of seats is not an opening.

Integrate player tunnels and dugouts into the relevant lower stand:

- keep the tunnel mouth recessed into the first rows;
- connect it to a flush or gently sloped access surface;
- place dugouts to either side only when supported by evidence;
- keep dugout roofs below the first-row sightline when documented;
- make glazing transparent enough to reveal frames and seats;
- verify the result from a low oblique field view and a top view.

Broadcast and press zones belong to a named tier. Include deck, desks or proxy
modules, camera platforms, commentary booths when present, rear access, doors,
stairs and safety rails. A floating desk array fails the programme even when its
count is correct.

## 4. Seat-written identity

Build words, flags and bands by assigning materials to the existing seat
modules. Do not lay text, a decal, or duplicate seats over the rake.

Recommended method for a batched seat mesh:

1. Preserve stable `(row, column)` indexing per section.
2. Convert a glyph or flag mask into occupied seat indices.
3. Exclude aisles, vomitories, tunnel openings and accessible bays.
4. Assign the white/red/club material to every face of each selected module.
5. Store phrase, direction, seat count and source on the object.
6. Render the whole phrase from the pitch and from an aerial angle.

Never trust transforms alone for reading direction. A correct mask can still be
mirrored from the intended spectator/camera side. Do not accept a pattern when
only fragments survive at the edges of a central seating void.

## 5. Pitch apron construction

Do not model the border around the grass as one flat grey ring. Resolve these
bands separately in the pitch `(u, v)` frame:

1. marked playing field;
2. green runoff to its verified extent;
3. grated drainage channel at the grass transition;
4. curb or narrow contrasting edge;
5. grey circulation/service pavement along the touchlines;
6. technical overlays such as tunnel carpet, dugouts, cable covers and TV paths;
7. broader end aprons, painted identity surfaces, or former-track remnants;
8. lower-stand face, fence or LED boards.

Measure side width and end depth independently. End aprons are often much wider
than touchline circulation, and club marks may occupy only those wider zones.
Use shallow layered meshes with clear z separation to avoid flicker. Add
expansion joints, drain grates and removable covers as batched geometry; do not
fake the whole surface with a high-frequency texture.

Pitch-side LED boards and fences must leave the player tunnel, dugouts, camera
lanes and emergency gates unobstructed. Validate all clearances from above.

## 6. Validation

Require all of these when lateral stands or aprons are material to likeness:

- aerial: tier footprint, apron widths, end/side asymmetry, tunnel clearances;
- low view toward side A: row continuity, portals, tunnel/dugout integration;
- low view toward side B: media/press integration, access and guardrails;
- oblique corner: relationship among end apron, side apron and stand breaks;
- held-out azimuth: no floating decks, duplicate benches, mirrored identity or
  intruding support volumes.

Print and report:

- tier count and row count per long side;
- portal and stair count per tier;
- accessible-platform count;
- tunnel and dugout count/location;
- documented and proxy press-position counts;
- side-apron width, end-apron depth and drainage continuity;
- coloured-seat count per identity mask;
- every inferred micro-layout decision.

## 7. Animation and reporting

Stage structure before seats, seats before access/safety, and operations before
identity. Reveal an integrated tunnel and its dugouts together. Reveal apron
drainage and pavement before field-level equipment.

After changing an animated scene, rerender from the earliest changed stage. Do
not splice only the final frames if an earlier apron, seat or access stage also
changed; otherwise the object appears without a construction event. Preserve a
backup before each mutation family and keep the rejected experiment hidden or
removed from the delivery scene.

In the report, separate documented programme from inferred spacing and describe
per-run polish as a correction, not as generic generator output.
