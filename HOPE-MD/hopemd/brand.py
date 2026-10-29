"""HOPE-MD brand: the mark, the palette, the color convention. One place, imported by the
page (via /api/site), the report, the video renderer and the desktop launcher."""
from __future__ import annotations

NAME = "HOPE-MD"
TAGLINE = "one spec, several engines"

# ---- palette (light / dark) -------------------------------------------------------------
PALETTE = {
    "teal":   ("#0d5c6b", "#5fb3c4"),   # brand, the interface, the box, active controls
    "green":  ("#146b47", "#4cbf94"),   # brand gradient end, "good"
    "rust":   ("#c2603a", "#e08a63"),   # the thing being simulated: ligand, peptide, partner, restraints
    "amber":  ("#b07a1c", "#d9a54a"),   # warnings, queued
    "ink":    ("#0f1719", "#e6eeef"),
    "dim":    ("#5f6f71", "#94a6a8"),
    "ground": ("#eef3f3", "#0b1113"),
    "card":   ("#ffffff", "#141d1f"),
    "line":   ("#d7e2e2", "#22302f"),
    "wait":   ("#e9f0f0", "#1a2527"),
    "receptor": ("#b9c9c9", "#46595b"),  # the receptor in the viewer: gray, always
}

#: One color per metal, and the same one wherever the structure is drawn: the Runs viewer
#: (web/app.js METAL_COLOR mirrors this table), the videos and the report figures. Element colors
#: put iron and copper in neighbouring browns and drew every ion alike, which is what this
#: replaces. The salt keeps PALETTE["amber"]. (name shown in a legend, hex)
METALS = {
    "CU": ("Cu", "#b87333"), "CU1": ("Cu(I)", "#b87333"), "CU2": ("Cu(II)", "#b87333"),
    "FE": ("Fe", "#1a7a3c"), "FE2": ("Fe(II)", "#1a7a3c"), "FE3": ("Fe(III)", "#1a7a3c"),
    "MN": ("Mn", "#9c27b0"), "ZN": ("Zn", "#8d6e63"), "NI": ("Ni", "#4caf50"), "CO": ("Co", "#e91e63"),
    "CA": ("Ca", "#546e7a"),
}

CONVENTION = {
    "receptor": "receptor", "ligand": "rust", "partner": "rust", "restraint": "#1a56db",
    "box": "teal", "contact": "teal", "warning": "amber", "error": "rust",
}

# ---- typography -----------------------------------------------------------------------
FONTS = {
    "sans": '"IBM Plex Sans", -apple-system, "Segoe UI", Roboto, sans-serif',
    "mono": '"IBM Plex Mono", ui-monospace, Menlo, monospace',
}

# ---- the mark: a hexagon in the teal-green gradient with a rust trace through it ---------
MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 62 62" width="{size}" height="{size}">
  <defs><linearGradient id="hg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0d5c6b"/><stop offset="100%" stop-color="#146b47"/>
  </linearGradient></defs>
  <path d="M31 6 L53 18 L53 44 L31 56 L9 44 L9 18 Z" fill="none" stroke="url(#hg)" stroke-width="3" stroke-linejoin="round"/>
  <polyline points="12,40 17,24 22,36 27,30 32,38 37,26 42,34 47,29 51,32" fill="none" stroke="#c2603a"
            stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="12" cy="40" r="3.5" fill="#c2603a"/><circle cx="51" cy="32" r="3.5" fill="#c2603a"/>
</svg>"""


def mark(size: int = 46) -> str:
    return MARK_SVG.format(size=size)


# The mark as a 256 px PNG (docs/brand/mark-256.png), base64, so the desktop launcher can draw
# its window icon and banner without a file beside it. Regenerate with:
#   python3 -c "import base64; print(base64.b64encode(open('docs/brand/mark-256.png','rb').read()).decode())"
MARK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAQAElEQVR4nOydCZwU1Z34f6+6e4ZhGGYG8EZD4v41MWyWXUmA"
    "QUCz/8Ss8TajRh2mh1uDEUW5FBlRFBQVI3I7B0SCjPEg8Uh289cBAcnqqolR4+oGI17rwMwwXDPdVe//e91d1VXVVd1V1Ud1"
    "d70vH6a6q6qrq6ve+53v/coPHA7Hs/iBw+F4Fi4AOBwPwwUAh+NhuADgcDwMFwAcjofhAoDD8TBcAHA4HoYLAA7Hw3ABwOF4"
    "GC4AOBwPwwUAh+NhuADgcDwMFwAcjofhAoDD8TBcAHA4HoYLAA7Hw3ABwOF4GC4APEpFQ0MNAeFfQASgQN/o2di0GziegwDH"
    "U1TWT55DAWYRAicBxVeENQEUAQCfEUof6W5tvh84noELAG9ABgYnX0eA3EOpdBrBTk+x8xstURh8TAm942Bz8xPsDXCKGi4A"
    "ipyqhoYJVBJW4p0eHunOeMdVnT26U2y9BgLvoFiY2dXc3A6cooULgCJlQN3k4T4fWY6d/Xyi6vTGS1C8AeYMoKWgWsJLkl+4"
    "5dCGDe8Bp+jgAqDI6D9p0sl+idyLN7YOO7WgKHq15o/09mRHkU0CxQOQ8JMbSwS4vaOp6TPgFA1cABQJ1dOmVUohaSGV6GyN"
    "Rk+q+dlSii5lja92D+T95C9hLyS43xfuW3LgiScOAqfg4QKg0Jk2LTDwmHgD9tWF2GUHKwpf4+Jb8Pk1JFgAWgh04Na7uwKB"
    "1bBuXQg4BQsXAAVMZf3UKylI92Hn/kaCL59S81PD/Qw7f+StcmDVEj5C52BBd2vrVuAUJFwAFCAV9ZPHCgArsAuO1HfZpD6/"
    "qeZPofHV+xkej74uSDCrs7V1J3AKCi4ACoiKiVPPIERajr76RbJZ71jzg6zYU/j8Ko0vfz7xvfxxsk0Uxdt6Nm78ADgFARcA"
    "BUD5lCkn+EOwGDv9ZLxlPtCk6lL7/MqAP1NSWQAqzS9DDD4efSPimw0ikIWHmpu/Ak5ewwVAHnNCXV35UaF0Pr6cjR2rn9Lp"
    "09H8Jj6/nAWIdnYw8fnNNL/B9wM9gqsfKg1LS7/ctOkwcPISLgDylMrg1BnYme7CG3R8XD9TMPL59X00tc8vY8H3Nzqefpn0"
    "eORLzB0u6m7euBY4eQcXAHlGRXDypaiJl2IfOlPRrBnR/Nr98cUzEvXPA+gFQfAvwxWX2vf5wXB7fFyBemQhvI9/53U1b3wO"
    "OHkDFwB5QmXdpJGUkJXYe0ax91o9atHnt6b591BCbzrY3LxHvXJgQ8MoQuERfDnK1Oe3ofnNzp99PxA6s7tp0+vAcR0uAFym"
    "sn7a6dhBlqHmvEITnQcjzW/XAgD1GP+/CgKZ39XU9Eyy86kKBi+nINyHZvsZBnl/c58/QfOrfofhflIbiML87tbWj4DjGlwA"
    "uETFxImDCSltxC4yHd8G5PUJncdM81v3+b/A/3d1H+5ZD21tIlihsdFf+fHHU/HVIjzoCc41f/LfgacdwuUaMQx3YepwP3By"
    "DhcAuab25rLK/odvQQ04FztFRUJeHjLj8+PHD+FyeakYWu40Ch/JQvh8twmEzMbjDkjt80c0exLLxeT3ADlICSyrloSH9ra0"
    "HANOzuACIFc0NgqVf9vXgO1+Mb472VhTytjz+TV5fkrCaL6vFwVYlKk8/ICGhuN8lC5G330KfpPfuuZX/R59iEJjySi/51P8"
    "s7Dra//QitdLAk7W4QIgBwwMTrmASrAMNelw0xF5GfH5ydNAw3Oy5VdX1tefDgI8gOd5mW2f317s4s9oR8w92LTxReBkFS4A"
    "skhVcOoIidKV+HKsWb49pc+vX+pSdRAVIiyynhDZzxaRjAFQzBjQUWD4e5L8DgMhYHRdYrSDJM7qatn8FnCyAhcAWWBQww2n"
    "hsW+Zdi4r8ZGTpKOxU+q+VXz9Y015fuSROf1bGx2JbdeNTl4KZWkpXj+Z2ZG86tiGDFo9AJuFmho/oHmLZ8AJ6NwAZBBKq+5"
    "vpr6w3dgs52Jrbgkujaq3dLz+ROi/58TkBq7jxx63HJkP1vU1voqB5Sx2EAjvjsxulLVmSGJzw825ioQ0ouLlUKvtJgXI8kc"
    "XABkgtrakoqyypvQx1+AjbvK0iy8lJqfGmnMHtz/gYGHe5bva2s7CnkEyxj0+oXZeH634XkOcDxiEYzcCPk6RTZ0gkCWdHUf"
    "exSFXx9w0oILgPQgAydOvxaj7kvwSp4Ghnly3QfAvs+Py5BA6bqwQO/K9xl2kYwBERvx5TQ8cb9Nn19H0v0+xiPd3tn0y81g"
    "/YAcHVwAOGRA3dQJPoGsxEY9PPUIuRSaP0neH188BeCf1926rqBGzLGMAfHBMoxhXGFL86uFBEmsT6BKGcoX6C2QhFldzZva"
    "gWMbLgBswsptC0RYjlfufEgyQk419j2CXZ8fN+wkIp3VvampoMfMV06qG8l8dxKZ42DD54dkloI2CxJ7+yLel9n7N2zi5ctt"
    "wAWARSLltkXfEmxsE7ExCyY+egZ8fnhPYLPmWpu2QRFR1TDxEkpgKf7Ab1r3+dWdnOpziEZLCajU6gfpjo6mLbx8uQW4AEjB"
    "kEmTKnrD/gUCwE3YKMsMnXUF5z4/8jkhdFH34Z4m1yP72SKSMSidjB27Ed+dlLhDKs0PKmEABtdfPgw9SgV4OCD1W9rR1NQD"
    "HFO4ADAjUm6b3sCGpgIRBqvHvtvy+dUj5ox9ftZA7x945OCD+RbZzxZDa2vLDlWU3gog3IYxgopEn1/9XtL6/DRxLoL+fkD8"
    "eB0gSYu7Tvt8NTS+EgZOAlwAGBAtt03uxdZ0urIymeZXNzrd7rEdEjQ/vgjhi7WUhhq9OhOOZQz8JLwIr8s0vD4BOz6/9vqD"
    "gSWmuQMfUkLmd2944ingaOACQEVF/YyxhEor8KqMtKJpbOex4/nsNqC++YUW2c8WldPqT4ewyEYU/iR6gUFtQiXx+cFQ8yde"
    "d0UUvC5J0syDzVtyMmS6EOACAORy22Q5tqKLEnzQlD6/seZP9P0j73cSqfAj+9mCZQwwDrICL9RYZWWCEEh2hNQxhNjW50RB"
    "nNOz/knPly/3tABg5baFXmExtq3J2Cx8cU2fhuY38Plxy18oJfN6Nq7/LXBSUjXpuovxqi3Fy/ctOz6/+tmG2phLPNWqum9h"
    "XG4IiaE7DzW3ebZ8uScFQLTcdv85mDKajZeg3FBzZMbn/wyXd3Z/fWgzn99uE5YxqCiZhJ30LrzeJ4F+oEQKS0xPwv2JC+9D"
    "VKLLS0OB5V4sX+4tAcAaVb+qqVTANBSlJ0CK6LKlsewJKT32XjgIVFxWcbTy4X1tD3sisp8tIhmDgf1mo7Cegxe2wtQSM/X9"
    "U467iMkW+gVmexZ1dofcn2CVQzwjACqC0y4lEizFl2cmblVpEIc+fwz2pNzVlPYt5jXuMguroegLhBvxiqtqKDrR/EniilGh"
    "8R5mHud1NW0pqoFYZhS9AKismz6SkkhRjlFKQCnWLKz6/ImWgUrTRPPUbPVWQfDP62pZsxc4WaOy/qrTiT/AqhbXyj5/YvTf"
    "0Oe3Zdnhlp2SBLO6m7YUdcC2aAUAK7ctUYKpJfqT5HvG9INzn3+nQMjMrpb1vGpNDolmDDBlC3Ss0XZTnx/iwkHZSlUTkCDh"
    "9m+lfpjbtWbLXihCik4AVEz82WACoUa8idONB5eY+/ypo/1xDYMr3sFYwtyDLRteAI5rVDdceyEIsAzvy1mZ0PwmsZ0QLleL"
    "fSVF59oVjwBg5bb7Hb6FEjIHb9vA5Dun4/OTTyWgC3u+PpRXrs0XWMXlv3/QIJBoxWXjwVhq4QAGg7YMbn8CtBv/LK0Ml60o"
    "lvLlhS8A8OZX/M8XQfQJF+PNPkU/nzzq8wPY8flNNEI3LpZ2Q3gF8Nr1ecnQm2vLDncHbsb7ORfv10D72RzzEZ1yA4nNVdhH"
    "CSzsWr+lFZJFIAuAghYAA+umX8AeKIG3ZXiqaLAWasfnZ2WnVmFk/x4e2S8MIhmDkvCdePuux7YRUDQ/gGawlonPb2AJGGQb"
    "om//hPHfOQfWb/0dFCgFKQBYuW1RJCvwJk6Qb45GUmtw7POzarRbCPEt4JH9wqRqxtXDSAhTv4RcKff5dOs3yMORVeMH8IXQ"
    "DhKd2fn4k+9AgVFQAiBabltaClT6aexuQEY0vyLyoy8IhXYiwCwe2S8OKiddPVIQYAWK9LHxfL8qoGuKhfYlC4HoTk8QUVhQ"
    "SOXLC0IAsHLbkl+6A/v8z6gklcbNN6Iz53QBOwJ28/x/xsXcgxvX8yfSFCHVk6/+cdRlJN+26fNr36uam9YyiIwL6SWC8AtS"
    "6r/nwKP5X748vwVApNx29U14kgvwVKviG5xofnVAEDS/HG/iPhDIwp5hp2zkkf0iB4PGVZ+8HyTRjMEpbJUNn1/7Xt7XOH1w"
    "AMXBkgNdsDKfy5fnqwCIldsm92Dn/BoxkLTGdrz8adD4/KDy3XQ+f5dEYWmPEHqER/a9xbBgsF93oPdm1NjsKc2VVp/NCKbj"
    "iE1iSgB78c+Czg1bt4B1jZUz8k4ADKi7fgIBaSVeu+GJWx1ofhmNrKB9eHceIyH/3d2bV3cCx7NE5xiEFmLgh2UMSqz5/KBS"
    "MsmOrmmvb4FEMFC4dSfkEXkjAAbUzRguEFiOkvN8o7ytmc+vCeToJLSBz8+C+7/y+8R5B5qb+XPmOAqRjIFI7sMY01VEDjBb"
    "8PnBLKakCTSqLAuJviCAcOv+DU/mRfnyvBAAA+um3YOplNsVyWthRF5yEn1+vPh/wJztrTyyz0lG5ZTaswkRHsBM0HmaDQma"
    "P1l7TN1eUSjcc2D91oXgMq4LgIF107fgxbwq0efSR1fNo/2QxDfDz/0Jb+acg5vWF+xgDU7uqZ5y5QXYfthzDP7Rqs+fVPPr"
    "2nV0KTQfWLd1EriIqwJgwHUzrsD87FPqi2l8RnZ8fkU4fI6XeH7PxnWtwOE4pGrq1UGMSd2L7eqk1D6/jI32KggXd67d+htw"
    "CQFcRBDoNKoaWSX7WFGiS0rVUkF79aNTOuWbolgIXbi47aAQ+gbv/Jx06Vq/paUy1P8bqLNvwwbWDarmGW128RVEWUZfRduj"
    "qvUq7VRe4gtJmg4u4qoFMLBuRjdGXweaa36GFWkaMb968RArIeRbwiP7nGxQef011b5Q+E4J4AbsuyXxLVZ8fvVWVYAa4KvO"
    "9U8dDy7hrgCYOINmyOf/pU+C2zs3rfs7cDhZJpIxCEt3Yzu8LrXPD5BqDgIKANf6oXsCoPbmsoH9jhwx9/lTR/3xYrdT6pt5"
    "aNOagpuEwSl8qqbWjsBOz6oSTbD2CWPhMLBvQJlb9QX84CbRsdNJNb9Rnh+3s1Te3IOt638PHI5LdK1vY+3w3EHTa3+EzXgZ"
    "tsvv6MuOWalM5CbuCYDyTgpSqRIoATlgIgf2ILrUDQf4O/6/o3vjuk3A4eQJB9a2vYSLl6qmXVmP7fUebL1DZS9VPXhNHfBW"
    "JiARV71wF7MAh6tjQVR1tF9Gd1HiQmE+5vN55+fkJV3rShCcDQAAEABJREFUtrZiO52nTDlWRfvjMS7dMAKXLQD3BACzAECV"
    "KtGMrNJdlJgkjfgLHE5+Ixpp/rg7wC2AKDoLIO7zq9C5A2zoFHA4+Y3PSPNHOz1JHEjo2RhADH2qT+fzQ4ohghxO3qHx+UGl"
    "+UE3tRi8bAEoxKP9CroRfsknXnA4+YX2CUOJGl8d/fe4BWAa7Yeozy8veefnFA6mPr/sBoC8pOCyAeC2BZDc5ycaC4DDKQxM"
    "fX5ZKADERw66rNtcFgDJfX7FPLI8C4vDcZ9UPr+68KjnLYD4wsDnV6cIuRfAKRAUzQ+QMOJPrjKsvAd3cd0CiC8MfH5l5BS3"
    "ADiFg1m+X54oJGv+fMhtuSsAYmZSFL0FoNL8slfARwFwCgBDn99A88uxADdxrUsNZX8ifV6V5zfS/BDfDHwcIKcAMPT5TTQ/"
    "cdkGcE0A7GN/Uvj8mnlC7KpyC4BTAJj7/ERj0Ea2u2wBuDsOQOPzkwSfX706clUlETicfEfv8yvZAFXWy/MWAIPoLAC9zy9r"
    "/rgF4AMOJ9/R+/xE0fzxEa80FvPyrAXAYgAH1RaAgc8fmy4ISsnlLFoAu2rHlIXKyVz8ttH4deypRFX4pX8TQHqW+Hz3n9O0"
    "swc4HAsk+vzagqGyMIA8SG7lRQzAzOePTReMVwzKkgWwo27saeH+8Bp+yyL89vPx+07B/+X4tcMpEe6QJGnv9oaaS4DDsUAq"
    "n18WCpAHw1vcDaul8PlpTCrIFysbFgA7suSTfoff8x3zvcggKtEntl939knA4aQgpc8fcwsUJeciLscAkvv8movFyMLZ7gjW"
    "XIXH/2aq/ZhFQP2lrcDhpCClz6/KennWAmAxgGQ+P1FpfkWSZmEcAB77Fqv74jn8YEfD6FHA4SQhuc+vGSXk7RiAer603uen"
    "igUg+1SZP9uXg989EY/9XTufoZIwEzicJMidnpHo81NVYNvDMQDFAlD7/FTr8+uXmbYAfJL/J2ATPJOrd00eMwg4HFOozucH"
    "rc+vLMG7swGjFgBofX6i9fkTUiWZPlsCl4JN8BT94TC4+jw3Tj4TDVQrGW6VLaD4/KrIoGcrAkXGAbAXJppfmSihjRNmjNen"
    "nV15pI/8KziBRATAfcAx5ZWJNWPwln4fCMWYCf0EKPmv0oD029Eb9nwJRY0vHgOgiXNd5PXxwLeH5wLo8/x6n1+dQsn0iKnD"
    "vYELwTHkazuCoy8CTgIvXztq6PZgzbOCALvwtt6D9/IiAsINeI839IWED7YHR8+AokY09vl1FgBVFwxwEZdjAGCY7zfS/Jke"
    "M42N8jJIA4nyYKCeVyeNrfAFfHvwpfGgKUIGYpNbvSM4poitJ5/O59f5/mrNH2v/bpIfMQDQWwBRZM2faQsAzf8AHvECSAMU"
    "Vz/YMWnkN4CjIErSClycnGo/vKPz2utHXwlFiTxYTavUIug1fx7EANy1ANgLYpQqUfv86hpqmeFoX+BH+IVlkA4owqkY4FZA"
    "DCYM8R5NsvwBQh55u+475VCUpPD5Fc3v4ZqA8RiAuc+vDKYgJKMWgATEdvTfhMnv1J5VAhygUsksO/vjPT+xWxjwABQjqXx+"
    "xfcnmY1sO8BlCyC5zx8vnZzZEVMsMAWZAH3a/f0r68DjoEvVH+/VRLALgeuLbmQlm6+W0uenqiW4issWQHKfX1tUITO8Ehw7"
    "DhfHQYbAU7M8lLhYORwqqcN7WQkOkCRhA20solpPLASgTGs18/kJtwC0FoCxz68pqJghSSlQ6WLIKOSsV4OjRoOXofAzcAib"
    "cr1j75jZUCzELAAwifYTtRvALYAoZj6/5jFKmZKUhFwOGUaiPs8GA3fUjToHhfU/QjpQuKu94bunQjEQsQDiCh70+X5ZGLis"
    "+WVczwIk8/n1y3RpD45hDTXjqTs8/6u8Oj+A+jIg/DAjQ6h/PRQDEQsg7uIb+/zua34Z1y2AZD5/fJkZgYmHyVT0X3tcNj9A"
    "pEU+wi2R16aMOgFl8xWQEcj57Q2jr4JCJ2YBxDOB5j6/HCh0E/fnAiTz+ZX3GRICFC7JnuQl0/DPveAhekO+qUz4QYYgkvDw"
    "q5PGvpCr+ou7Jo45JSxQdAmFsdgGA4SSD8Ww+Oh5T+zZB05hFgCbtSrHAeVydgaaP5oi9PJIwMgrGjf/9T6/epBQmpKS1f3D"
    "A50NWcN78wPwttwAmYTASaIkLoUcsCM4pjYkwF+wC/wC37KqUJfj98/xBXzvbq+vuR6colgAss+fqL3kdg+apTvkQQyAxN2A"
    "BN8fVAVD05SUAs2K+a/GS/MD2oOjWYfJeI1ENnEo22MDtgfHPIwtbKtJ6rICT2LV9voxv2EzRsEuct3aJD6/nO2KR8Hcw/W5"
    "AIr5D4k+P1HlUdO1AKQs+f9qPDU/IIvCLptjA1C7r8I7lXrUIiEXHukt/dPOYM0IsIMyFSCVzy8Pb/dyDIBSbQpQ5/PLmj9e"
    "I9AZO645p5oS6TxbH6LQiX+/whM6w/Jn2PwAqYTlxLOe12ZpM0pLhgUo/Wxs666PIIfsCo75ZhjA3vW0QXxswO6MDhVGrf4Q"
    "G31o+QMETsP+/OaO+pqbxrXu+oXRLnsmfm/wMSocTwQY5JOE7p9Db8XfpBJUrUx+mfn8UeFAXe78DNcEwD78V4kWGFXXACQq"
    "l0mzXvVwEAdIAfEiB5OJnqWEvo2fW2HnQ3iWk9B0XDhy3RtHIAu014+ZjBdiBqEwkjUhkURM2iOE0ody9QCTMBv4k2XLFe/6"
    "4p2Ta7aOfXzXx5ABop2f3AwOoAQewRTyBJ8gBPv5j4lH+gJX4j24AM/xB70AVfKlENH8fzjUBUexEfzZVwJvCqWwi/SDbiYM"
    "VFpNk/L2bgxgqInPDwmaPwOzpmyb/xSkZ8tL+lrwJI7a+RyeZtWRY4FrIAtgR29hhTXwWozUfWt/9gATUZT2MGsHsgibwYd3"
    "IwhZBq9jv3CYroYMsD1Y86DTzh8/H3K5JEofHOkrQWtLaMY1tSTy9KhEynDD96Q+mB7ugVWhDvg38Yiq/epKhbuMi2Ow96X0"
    "+RXND87TpS8Hz+2Hhznf1oeAHpFIv9+jFu/Gr90EdhEy7x9j538GL0x9sn3wen0LrZ0nIIt0+/rX4fUcADkAf8+/bZ9YY7tw"
    "qxq8bvfjIs35GlTWTifinxPtfLIcP8sEwfK+/XC6FAJ14M/tcmAMFwXA0JQ+P4DWLXCCQI/9kGlIsMfvz2t55VjklURtuQAx"
    "/qm9YUwNZIhoBR1rU5hjneY6yBrkRrAJ3s/H8B7uBgdQgT76cnBElYOPRqL9eL63QRpQWU+n2VlPpyLcju7BP6JlIAf+4oHt"
    "veAWLmYB9sUnAplqfqKKCYAjCLFf+gt9/2fl1xM2vfYertkFdkljgowapgFZBR07n8HzX+EohZWC9vqa8WzyE9iECNIqgYaD"
    "eA/DYBNWN0CAMttjA2Kd31aNAj2Rrk8BMlWMZhDmohaFOuFbNBR5H5cpw8AtXI4BgErDG/n8cd8/unTwbEAKth/qKfT6t6nf"
    "Y8BtJdgEG8012xv+Oa1px6yyLt6hNrAJXsPBh3tLHoSMQx0INdo+vnnPu+M2/vEDvChLwAF4LadHroVFMOB3b7qdX9Y4mTbT"
    "/fhr5vV1wWAaVim1veAWLscAQBX9T9T88aWMvacDRzQWAVtBMfy+l8dtfrVTvW5c6+5fAUsJ2kXqNwUcwtJ8eDm2gUPwWk5m"
    "M/UgQ+ysqzkej2m/jp9EFOE5oWVXIy7+BxxABNpkZb9I5ydkPqQJRv4zpvn1YO4LZoZ6FCXnSQsggqnPn2gBRLFnAWBW3rb5"
    "jxfkWZNNtmerYSP6uTYTbA0WaSfU/xL+9iGQBtTn20BrISPPVJd8YHt4LJrQX4zbuOvXmpWEWq8bqP4YkG9iKi6pKxSJlWSg"
    "80faXZYD9P+MbsAIEZOIkeaxF9zCXQFgwedXsgARSWmvLVMH6b8wCW01Wl/iF3+BR7T1cDLmv2KjtO2CdPvKf+3E1zbgzO39"
    "a9LXhihEJLA/21EAskY7Fg5gfPPudrynG8ERpHF3cPQwoy2s89uNlST/quxH6CeFD0EgotSGgVu4KgAUzU/NfP64DxY1x6xb"
    "ANsnjvoOfmYY2ADP583zWv7zC6Nt7Ik21Nw6SHJMYktztgdrlrKpsZAh8PLdvbO+5nRIgx1lNZcRm+kvRhhCa43WS319s2Mj"
    "LW2BLaC0D4R1+vUY8Fucuc6fu/H5p2Jm4GyJxUX3glu4KgDUI/yMfX51qsSeBUAEn23zn6To4IJkf2AK/rQfWp0f0F4/+lo8"
    "h7mQYURCN0A6CNT2uAa8b1vNhOl5v3qjA4jkKDeP1+cH24M1V8vvYynShZAGeK4fUhI6DV8+CjnmLKkX3MTlYoypfH5QYgNg"
    "0wJwNPmHSkkFwLiNr/0HnortcfeSFPh5qn1erRs7En9rM2QFcu6OYI2j6sXtdaO/hZ+fADYhAqxKtn18y2stjscGULqSjQ1A"
    "a6kxXc3POn9pQDpnQvN/fjK+ZReL2TwHOeQb1HZmNKO4LACIKgUYXRMvnab2/e2Nmd45ueZrKC5szeLC4+8dv3HPn1Lth8f9"
    "BdiG1LPS2WZb2fmKAn0R9wtAlsCr96CjATU+cKD94R3m66faz+cjDeAAlub0QdlOvBeLIB1QmEth6Tz1A0tJjsx/mSqnA1wy"
    "RF5YAIa+P8gTJ0C1tEZYtD/3H6O+z1jZr6y0t9XR/IC+0muNtr1ce9aAcBh+m27E3wLHCbRsuZ0PsGf9YSqjHmxCiPSYlf3O"
    "adr5V2z+d4Ej0gySYucXw+K5BtV/hkEOqaYOxrZkkLywANRofX5nU4Cxw9kf/QfEUoCPzQ/AFv5LsA2dlfidQHzllU+x6a+Q"
    "A9jYADslzEVRmoifsfn4LnrEf5i0Wt178OGue/Ge/w1yCLP2/JROMCr9hdtsBzvTgV3cqb0dGR+1aZU8sQDiaH1+atsgi1Tn"
    "pTDezmewAe6f0Lpru+X9RelhsA05a3v96LHqNTvq2USVzEX8rSCBr8Xq2AC0wuzPoKPQVNO227KFNLzt3T6MFzhyBZzAOn9A"
    "gnNqNu7+1Gg7yXFIvhO/cX3pkG5wCdefyGKc7yfKMoINKRCS6EV2x29iQ7dk/stE5gdQeBVsQglRaui1N4yux/O8FZxC6X+A"
    "MyyNDXh14qjz8MLYTh/6CVgy/9VE4wXUstXglIilQcLjzTp/FPIF5JAu4m4XdF0AaPP9NMH3Vy8sHY86evCn7fw+OGjo8vyA"
    "yMM0qNACjqGvjG/d/QO8Lo7Se2xsQKrUpER8tsf94236fzUtu98HB5RKoqOxAVZhmh+E8AQW7U+x32uQQ/YKrtXkiZAHFoDe"
    "51drfnX0P7UU2FU7pgxFx4/ABvj1h8a37noebIIpoy14bl/a/RzQsiWSINgXOMrn4SPS54s83UgkR25zdA7AUpMlpkObt193"
    "9kl4C2zX+xckanvSlMyojX/cTw3iJJlA1vypOj/DD+QlyCFvEMbvBbsAABAASURBVHcfLp0HFoCRzx/PCSqFEy1Y9eFyOJ9V"
    "kgEb4P6/A4cIlDp5ms1UlsYCZ/T4Cb1Qnqx0XstbXeCw2AX+7u+bjg3wlTh5yMln4zbutuVK6ZnQunsjs24gg2Dn/7SE0O9b"
    "6fyMmpZdb+NnnD8XwAZM+b3p87gASOrzy7OlLBcEILbH3VOQHDdaHyVrIIcIIF6hN7EntOze7DQeYDY2gBKYBjbB25OR8l14"
    "TR3PoNQT6/znjGl5ba/Vz5CoFrLt3jlhp68fHM7xuAM97mcBkvn8seHBSonlZEdiZaQptfXgTzys6PP5HE+5ZcEkbGRpaT2r"
    "EAo3ndOy59+NtgUInYpXyMmY0uN8tP9D6hXs8Vx2x/2zQh+lATEjz/ZjFY7xTjdCmjAt7veTsXY6v4wEpSsiMYMschTbc4u/"
    "f8brDdjF/XEAyXx+tQWQIgaw4+Mx43C/gWDv219Ot4ounlnWtQVek7VmZakZrJETSp0NqCHQ8Epw7DjlrURsP+0Hb89T6tF0"
    "6TKuZRf7LX8Fh8Q0/zinFYVZOTgfSD+1O+DLKhLe0TX+ctiPAcB0n3eRLnlgAUSX0QWND8VUHqcUswBSSEoqOXjsN6GOtb8M"
    "RuP/4GR+gGUo3Tl+2O6UnXLckd2s+KWjToOm03o2NiAy7p8QW2MoGBKQVZBh0N0JggPS0fxq0Np6Da2ua+1OAbdCM3b+7f4y"
    "MBoHk2tctwCiC7XvH9P8lNqKAeAetWATEUTb5bZMvtvB/IDUsOi1SI5eSBohZSMkbSBKkuMBNWfuKB+zAHzkJrANfffclp07"
    "IMOwDoiLFjufiXX+czL1LIFYUDOjD3nZhn7/b/3lqtiXu7huAUQXat9fr/lBJQyMYXP/weZz6vAG/KfZdFW7BMJiCxsCCxkE"
    "z69boOKPYpF+S5y7cddu5i6AI8hi1HgTwTb2Hpxi68h9wi1W05zM7Bd8oQmZ6vwy41t2ryBCeAR+Q1rHZX7m7SUDoTlQAcqz"
    "MB2MdM00LlcE0klAtQUAcrEQkDOEpuNXncz9x1B3xqZ9jn5iz0FHzw9IAvqgtZFCmjaR4Og8lJcd4ARCyuzszsZQ9C/pzdpz"
    "CFi6UxDE8/GLDibdkcLnARHOHdf0uqN6gynPo+mPb5eEpO9ApF6AfZfgVaEUbiythndxSWniszDdxOWagDoJqPP5qWIBRFeb"
    "zZuSKLWd/iOCmNHovV8kjgfB6MHfPdMs4p8KZjHgpbNdu98RhD6erUegybDOJxCJCQGzqdpv+/wwpuaXuz+ELMKEPKsXIIgC"
    "e3JxC7bJ/022P4vy7xFKYFGgEpaXVEA3YSP+YvUvQPssTDdx7fuH1taW9QwYcERXG9xwKRcNwVfXHmxp2aw+Tnvd975OfH67"
    "kv9/8GamVSbLiPZgzXY81XGQFnQNmp3On08fY3v9mH/HC/d/IYv4w/T/ZLvjqXk1OOoHIviUCVWCRN9Od/CRU1jrfHXS975D"
    "Rf8/4Nvj8f0gzMR0P+/vd0a7v+zGD9GGo4IACc+4BPWzAaPCYOCxnrK98oNocoy7A5ENff5E3195MouBvST4/JfZNaMolbLS"
    "aPAMWTTcsQBgJcnHf313Rh4owsYG9AF5n9XRg2xA6R9y2fkZMavIkWWUaSKtEa0TfPm2ev2QGRdfi1mRGxUfX+70as2v8v1z"
    "XYBEj+tVgRN9/rjZT2QhANGlUQzAUekvgTwNWcDx/ACINI7/lsiRy61E/K0QTYPRRsgWBDLm8hQTzE2NB/iI1udXRf3jus/L"
    "4wBS+PxxzR/dro8BsLn/aHbZevhFZO5/8277j/qyiKP5AZEgl/RjOxF/K4w/vPsBSGNAjRmRev8tu3NaO69QYEpK7vxgovmV"
    "oW+UWwCgyfPHzHxZ8yuBkth2vQVARXotlcsK0dixUo4XIFn1GQMBNiPOeqSYDUcmhF4yoWXPf0OGYWMDnA6oSQolK40dMk7U"
    "AlA95wISNb8sFLgFkMLnjy+j+x1P+8q2B0cHtwfHbMWAW6cIwi8iwiHyP7KzfODIf5Nhls6n4log8vwASm1UDKLTx7W89gpk"
    "idiAmoyM02ewcf8BP83IxJ9iRLEATH1+iGt+5YlY7uH6cwGS+fzxVAmB8bQbVpKPl+MpN+OetbityuSgED0giQ8zlqsNOZz7"
    "b5fAEbKQlZtOuSMKigmtux+HLCPCkTmp0laWIXRrzeO7DwDHEMUCgCQ+v7Ik4Om5AFQ9AlAf7Y9ZACdCCBbTT+A2+BLD2dRe"
    "WWtZGChBRlZ6O/uwmnhEOFaDX5mkeCidM751t6O5/HaJxBYIOBjma0COpsoWKvEYAICpz69+Iha4i+sjAZWFIiqpYgkMo73Q"
    "CJ/CCEh3UlZMqFDpHcgR45vf/AqtjTpBJN/F37IAf842FHiP45n8TAyJp2Ku/wHIIdEMRdoptLezGUAtFlL6/OonYoG7uDwO"
    "gCpJgLiTFPX5T0LNv5T+Hcoz9ZjWyF0Q5m2vH/2H8a2v7YQccc6mna/j4nXIA0pAmtYHQjpjA7j2t0QSn1+xACC+dJE8iAFA"
    "PAbAVuJFGUhFWISavzzTz2hmY92J8Ex7w3dPBQ+SVt0ATFX6D1MHz0PwFnKq2tTnB6p1cz0dAwDjvP8k+ApOQQsgSxxHqD9j"
    "UfFCw3HdAGKv3r9XicYAUvj8qqyXxysCgdYcwsUZ9Aj8K6RVpMcC5PwdwTG2qgcXC47GBqCaIkIo50/OLVRS+vxqt9fLFoA6"
    "2i+bR9NQ++foqx+0+oScYoONDbBVN4CQbdmaaluUGPn8kfVUCXjHi2F7ehwAqCQghdNpH5wJuXpeOjnr1fLRac7cK1zKS/rm"
    "sqf4WtnXH6bOn2DkRQx8fkXJqWJeUdng6RgAqEUhfBsOQy6RQDgXPAp7yCkRjn4fX5pqdrwrx7B9np/rWX8Fj4HPr4n6q7Je"
    "3h4JyP6ozKTTc6b9le8fAR6GjVUQD3f9E178m1lJLfU21FxP+0X47oTWXb8Hjj0SfP5Eza8e8eomro4D0AZKAKpoGHLMMPA4"
    "57W9ewgXK2L/OZnAguaPBwPAVVy2AIjmGlSbFv3KDrl+FjzHC0jWfH6qzn27R55YANFrdFz2cv8mEHtzCziclAipfX5KuQXA"
    "kOfrydfoU8jtgxJRPuf0WfAcLyCl1vxEP/7dPdy3ACBuBR3OvTziAoCTYYQk0X695nfZ/oe8sADiUyf/aq8sfdrg5X8LOJyM"
    "IqXw+eNSQRkW7CLuzwVQFU94g+ZWAOANeAk4nIwiWPD5VZav58cBqMonfYAWwKFc+UWU9vUv6XsZOJyMIiXm+ZP6/J62AKKa"
    "X106+SXITWCeEmhmo+GAw8kGln1+j88F0D48gUAbCoAumvU5Oj2EHFsIHE62kKsCa6vdaOa+kDwIBLpcE5D9JZrHJx0lPniE"
    "HJ+Zp2OYfS+RprJhsMDhZAOVz6/t5FTl8xOl/buJ+7MBQf3stOilekMoh40wCLIBHn/ZhObXngQOJ1tY8vn1S3dwvSow0Szj"
    "1+5pYRC8AgMgo1B4dfywXQuAw8kqqXx+9Sghd3FNAOwrL6fy89FVJdLilgAuHxJOhMdgCITSvU6sog2V7hnXumt8pp69x+EY"
    "IaJ/Gc/za8f6y1kvufO7PRWY4ZoAGHr4MNFr/qiLFI8JsBW/EyphDhkKf4cAOILCR/hFPxzX+tpCkg8il1PU+AgV4nl+7Vj/"
    "6Nq4BeB2MRBG/lgASonweExADpR8JPSDG+FU2EyqvzgKYPWpNBjko3eeNPB/vz1u42v/ARxOlhl0/YUNmF6er/X9E31+QvLH"
    "AnBtLgCzAHoGlEF8oJRW88cDhNElFQhsgUEnbiGD4Aw49td6qeOV4dCbMJYfj3JYEIWXx27a+QbX+JxcMGjGj88XCFmGje2f"
    "omvUzU6VAtRp/nywAFydDER0Gt98qfKcKBsx2O/M24VTzsQ17bhtZufjm3L2xB8OR+a4Gy4YIVFhBbbDCTQ++D8+DBjiw93V"
    "2S5QjRFyWwi4aoNUNUykarNf8Z1oqiHS2igqfn6zEBDmd67b9HfgcLJM1c8u+JpP8t2LLe8aa58wiPqr3u5f/bxr/dDlZwPC"
    "YSOfn8jzqUHtOakCK0BUkhX/CuQaKUw/qJpUt7zy+muqgcPJApXX/7ga/fwH/ZLvr9hAr4lX9DFyNs19ft1jwVx92IrbcwE+"
    "0F4cve+v9qDkqcPyMnaE2IJEnndHZ5M+4ePKydfOGRYM9gMOJwMMC57bb/CMi+f4qfAxtr9bUPmUKnl+zVKNuc9PY5Hv2Ns/"
    "g4u4OxKQkk1UN2Y6QfNT7TBKqp1rqUq1yL4VrcCbtKxLCH1QNeW6ieCym8MpaMjg639c31NW8QGAtAxbUkXi2H7VzpG/iZpf"
    "1VxBUW6KBUA3gou42zluvLG06nDXLrwW/2LH5zeEqHdTDSwA8idJkuYcbN78O+BwLDLohot/hO1oGbai72jbkxWS+/yRt9F4"
    "4R/3i1+cA+veyHUxTM1puUr5lJ+eEKCB5VSi12mi/SBPEdYuVeFTSBg/nLDU7P4H8NFbu9Zv5lWAOKZUYWTfR30PYnv5PlG1"
    "LyVQrW9fConRfk0Wi+qyXkBbAj4654tHX3R1UlremMfVk+uGU0qW4iX6cXytVc1vdlP0RG7GE0SU5h9o3vIJcDgxqmZcOswH"
    "4hJsP9domp3sXqZqh2BtP2x/z4sSzOtc83xepK7zzj+uaqibAAJdgddwhCJ5Vaqc6gsuqjq/aZ6VxAOIse19uOIxWgJ3d6/e"
    "3Akcz8Ii+wEQ7pQo3IDtp4RSSaPpk2l+2VKFhDy/uj1GjydR+pYAvlkdq3/TDnlE3gmAGKR60nXX4KVcgq+/ZrwHqAStzkdT"
    "x2k0KxIkcxeuXVopljyyt6XlGHA8A4vs9/QbMAvj4POwXVQmTNJT2o8Vvz+pz78XxcDtHatf+JXFg+WUfBUAUWprSyoHlP6c"
    "CLAAL121PZ9fJ4kNYwrKrfsEd7uz67QzNkJjI58tWMw0NgqDvnyzHqi4mBBhqN5HT9m+FFL6/J24XNLxVf9Hoa2tD/KU/BYA"
    "MdjgHtLrux077Uy8qKVG5r/xL7Hgk6m3EvgzkcjczqYnXgRO0VE9/cIL2Jh9fDlcWWmo+Z37/Ngce9GdeBT80t0HHn3xIOQ5"
    "BSEAZAY1XH2qRPz3QXQIJrHu86veqzW/OiqrleDtlJBZPGNQHBx3w6UjRElcifd3rN6nT+Xz0/iIvRgGPn9MG+H7zRL0zT+w"
    "6vcFE2AuKAEgUxW8ZgQIwgogdEJkhXWfX0G7VdX5459mb7bQMF3Q1bJlL3AKDhbZF0BiCuMqokwpi2Hq8wOkbj/67bRdIDDr"
    "q1UvFJzCKEgBIFM96dp/w1twP/6K4UbZgETfP5XmN8zb9qHZuEoMkMU8Y1AYVPzsssEBUWQFYK7H+16S7D6nk+dnLiMef86B"
    "VS8U7ANmCloARMCgTtUn/x0kFBZTAqdEV5pL8ATNbygcdMvo9m58tbSNGmHKAAAK3klEQVQyXLaCZwzyk0hkv6ziFrxdc/HO"
    "DUwanU9H8xO6Dw3+Ow+cMLK10IPGhS8AZILBftX+8C1UkuZirx0IjjW/yfZ4TGEfrriza+iZBX/ziwYW2f/8jQYikLvw/pyi"
    "3PdkPr3s59nT/Adx89KKo4ce3tvySlEogeIRADEqJk4c7CsRF+H9moE3TSkkaObzK9sJJCQX5E/oFUXs/TsA0tzODVtfAI5r"
    "DJlx0YUSJUvxfn1bXqcZNi6TnuYPYYNY3UcCi3see2Y/FBFFJwBkKqdddTqR/EuxN//Eps8PlioTxRvZTkp8M3nGILcMmX7x"
    "SLz8K9AEG5v6vlnV/JrAXmRH/GRbmAbmdK15di8UIUUrAGQqJ109EoN4K/F2j7Ln88f3M0dlMxB4kvrI/K41PGOQTWKRfTZL"
    "78oERa6QEc2/h0jSzI61L7wORUzRCwCZqsk/vRQ79VLs1GdqNYOUyuc30/xxnzLeiPrw7WqxX9/dxWYqug2L7JdI0iIq0Rl4"
    "2QOamE4aPn9Cnp/C+4TQuR2rn98GHsAzAiBCba2vuso3lUqkEW/6CQnWXwy9D5moaVL4jAQORjIGof4P84xBegy9ubbs6NG+"
    "W/B6zsG3A9m6xJhMRnz+LwgljR37+22AtjYRPIK3BECME+rqyntLQ7dho7kVNUC5HZ9fr2mSjSXH7Z9KQO/sOuWsFp4xsAkK"
    "60FD+hrwOt6F1/1k7fW3EbPR7Wfg8x/CFPJy34C+5V8u//1h8BieFAAyrBhJCdDF2BYmY+Pw2fD5zXfRaB45pUT+AgQzBuva"
    "ngdOSoZMv+QiSoC5a2cZTNwyIIXmNx7jH0ah8HhJQFrodlEON/G0AJAZMvWqM8ISeQCFwMWWNL8+BpAyuqzsvlMUxVndTb8u"
    "6sCSU1hkH82kFZEx+5oYjFrzJ7PELPr8AnmOEHFOx2MvfAAehwsAFdWTrxxLCXvQA4y05fMDGGh+k/2jLbKN+oS5PGMQ5fhp"
    "l54uChQ1PvwkIdAKRj6//N6izx9P77wOPnHm/sde2AOcCFwAGDBo6k+vlKh0LzbC061qmsRcojoWALGx49EPsMaNsYEQLteE"
    "S0J3eTVjEInsh6W78LpMw2xMwELdhpSa38jnx+WHEkgLDqx+vg04GrgAMGPatECVdPB6vEALsRUNMd3PqubXoNZchGUM7i8v"
    "h4f2Pdzm6kMicgWL7B85GroVr9dthJXaBgN3C1L5/LJfBaY+PwqBDuz8i/ef0LMaGl8JAycBLgBSMGTSxRWiULYAX96EnbXM"
    "ps8PxvUI9DGFyO6fYf550YGThzcVbcaApWGrj00WBF8j/u6TNFF8Q58/Nk4D7OX5BUKO4uJh0keWdjRt6wGOKVwAWGTIpKtP"
    "DvvoErxgE7GxCfY0f+oYgnI4Au9SIs3rXPvr30ARMXj6ZZfg77oPX35LEY6g1fypfX7VGlkIyER3YIKzlfjIHR0rt30GnJRw"
    "AWCT6slXDScCLEdNc35ynx8U4ZDg2xIrcxBgp0+QZnWsLeyMAYvsU4GsREU+ylL+PsENsOrzw4tEkGbvX/X8e8CxDBcADqma"
    "WjuBULISW9/weL7fDAtZhNh+Bo27TQTf/O51T34EBQSL7IeFyJN1rqBql90sxQrOfH5KyFsC0Lwrt10ocAGQHqR6ypXX4lVc"
    "go3xNNln1fj86hiA1ueH1CPZlM+HgEprwyVSY75nDAY01B5XWhpqxE49DX+/39TSMfX5rVyfiPD4WKDC7R1rtm2G1JKVYwIX"
    "AJmgtrakulK4CdvhAryiVdqNxm1Taw+oOru8nRiEGID24H73l/UnD+ZbxuCEuh+Wh/qXzcbzuw1/xwC5U0fIpM9PoJNSsuTA"
    "/tK8LrddKHABkEEi5cvDoTuwcc8EVovOLJ+dUvOn8pXhczzQogNdQpPrE1cwsj9kcN8UiUIjnt+J9n6HdZ8fX/QSjCVIvtDi"
    "Qii3XShwAZAFWPly6peWYaO9OqbqTDANaOk0plpzaj7+ngR0Xtf6p12Zujp42qWXYj5kKZ7vmfGAZ7LfYdXnl6GxP2QzBhLn"
    "H1j1HH+eY4bhAiCLYKBwBKGwEjvBWDs+f2qfOcGS2CkA5CxjMHjaJaMogUfwe0c5O3/LPn+7SMKzugqw3HahwAVADsBA4QWE"
    "0Puxkcfq1ln1+eW9U/nMse2EPiVSaV73umezkjGoxMi+TyAP4Alepk3Dxzt17K3ye5TtSTS/wfs/o2Ux58CqbQVbbrtQ4AIg"
    "V7DKtfv+0oCaczF2jpMd+v7G5rR2v5BAyLrePuGuQ81tGZnmeuKNtceF+voWY8ptCn6dP+n52fb5Nft/SkFaWAzltgsFLgBy"
    "DBsHf/gw3EIonYuNvyK1zxwneZ48RtxQYENgH+hf7lvuNGMQieyX978Njzcb3w7QHF/x+eVUJRhE+7W/I8n5H8QPLq04erBo"
    "ym0XClwAuASbCecP+e7CXjGNsBp3Vnx+MNf8ZmPjcfkFvlzU2el73HLGoPFcf/VnVVMFAovwsydYskys+PwJmp+EJElaE/b7"
    "PTsj0m24AHAZVr7cR8PLsDdcIa9zVJNQ91ZrOkR65/vY/ebtX/vMc5CEwdMvuxy/n43ZP0O93tDnN9X8qX1+FC5tYSoUbbnt"
    "QoELgDyhcvoVI32UrMRONSrRZ07q84O9+gTSHgK+m/av+7WmKAaL7OP2R3CvUbZ8+lT76YUBhT2YsZjZsXYbr4qUB3ABkGcM"
    "nlp7GSXSUnx5hiWfX0Zr9uvSCdToIE+LlM4JUOITI2P2yaUm+6mOb8fnJ/oYxvv4QbRAfpPUAuHkFi4A8pTqqVfMwM52N0bF"
    "h2Sk7r2BUJCPK3db+5o/OvchabSf0g7MStzRsea5tcDJO3zAyUuO/dd7rw886+xVUiCEgTsyik2sAbVGlTWw/r269wKNjydQ"
    "lvoVxsR9fq1w0Gt++XjRTg9qt+UIvl8SOHLsyq+aXtgNnLyEWwAFwPFTLj4hRAJ3Y9+ahJ3LRyxofu0SVJE4tXmutyzsaH5q"
    "5vOH8QiPlwT6PF1uu1DgAqCAGDL1sjOoIDyIne1CBz6/Aal9fr0QSebz46vniM83p+OxZzxfbrtQ4AKgAKmecflYkOhK1Lgj"
    "kml+jdkO8c6ans9vuP/rIMHM/eue4+W2CwwuAAoXMmj65Vfi8l7shd+wr/l1+2veJvH5Vbvjmg8pgQUHVm/j5bYLFAE4hQo9"
    "sPbpJw/QId/E7ngzavb90WAA20S0nVm/1AUIiZLii2+PWxaxwJ5sSEQjgx1ApZ/vl048i3f+woZbAEVC9bTaSiDiXOyq8833"
    "SmUhWPL5l9BA3/28KEdxwAVAkTFk0sUn04DvPuys1+HNFagmJZhy/r3ZUsJlqxCgvNx2kcEFQJFSPeOy4YTCg/jyh0Y+fzzP"
    "D5Dc56cvEsE3e/+qZ3i57SKEC4Aip2rqZRMEH6zE3j3cZrT/LUEQZnWsfqYdOEULFwDegAyeflkdBgrvwdenareAbrAQ+VgA"
    "iZfb9ghcAHiIYcFgv4OlnTMJkFtQGJyk0/ifY3NYdmDts48AxzNwAeBRBt1wSQ2RYCRIIFI/vHlg1XO7gOM5uADgcDyMHzgc"
    "jmfhAoDD8TBcAHA4HoYLAA7Hw3ABwOF4GC4AOBwPwwUAh+NhuADgcDwMFwAcjofhAoDD8TBcAHA4HoYLAA7Hw3ABwOF4GC4A"
    "OBwPwwUAh+NhuADgcDwMFwAcjof5/wAAAP//rAw30AAAAAZJREFUAwD2Dbzs/Hv+oQAAAABJRU5ErkJggg=="
)


def logo_png() -> bytes:
    """The 256 px mark as PNG bytes (tkinter's PhotoImage takes them as data=...)."""
    import base64
    return base64.b64decode("".join(MARK_PNG_B64))


def css_tokens() -> str:
    """The :root blocks the page uses; regenerate style.css's header from this when the palette changes."""
    light = "\n".join(f"  --{k}:{v[0]};" for k, v in PALETTE.items())
    dark = "\n".join(f"  --{k}:{v[1]};" for k, v in PALETTE.items())
    return f":root{{\n{light}\n}}\n@media (prefers-color-scheme:dark){{:root{{\n{dark}\n}}}}\n"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "svg":
        print(mark(int(sys.argv[2]) if len(sys.argv) > 2 else 62))
    else:
        print(css_tokens())
