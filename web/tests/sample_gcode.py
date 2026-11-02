"""Generates a small, realistic G-code file for tests (no firmware settings changed)."""


def write_sample(path, layers=20, moves_per_layer=12, layer_height=0.2):
    lines = ["; generated test print", "G28", "G90", "M82", "G92 E0"]
    e = 0.0
    for layer in range(layers):
        z = round(layer_height * (layer + 1), 2)
        lines.append(";LAYER:%d" % layer)
        lines.append("G1 Z%.2f F600" % z)
        for i in range(moves_per_layer):
            x = 50 + (i % 4) * 10
            y = 50 + (i // 4) * 10
            e += 0.4
            lines.append("G1 X%d Y%d E%.3f F1800" % (x, y, e))
    lines += ["G1 Z20 F600", "M84"]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path
