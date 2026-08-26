"""
The Monte Carlo simulation and the statistics drawn from it.

Two things drove pulling this out of the task body.

The first is drawdown. The report measured only where paths finished, which
answers "what might I end with" and not "what would I have lived through". A
path that ends ten percent up after being sixty percent down is a different
proposition from one that drifts up quietly, and the old report called them the
same. Anyone who would have sold at the bottom never reaches the ending the
report showed them, so on a risk product the path low is the headline number,
not a footnote.

The second is memory. Every path was kept in a list so twenty of them could be
drawn. At the permitted ceiling that is millions of floats held to plot a
handful, inside a worker capped at 250 MB. Statistics are accumulated as paths
are generated here, and only the paths actually plotted are kept.
"""

import math
import random


def _percentile(sorted_values, pct):
    """Linear interpolation between order statistics, on pre-sorted input."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def simulate(runs, steps, mu, sigma, start_price, seed=None, keep_paths=20):
    """
    Run a geometric Brownian motion simulation and return its statistics.

    The horizon is fixed at one unit of time and `steps` divides it, so mu and
    sigma are expressed over that whole period rather than per step. Raising
    steps makes the path finer, it does not simulate for longer. Worth stating
    because the report is read by people who reasonably assume 365 steps means
    a year.

    Randomness comes from a dedicated Random instance rather than the module
    level one, so a seeded run is reproducible no matter what else in the
    process has touched the shared generator.
    """
    if not isinstance(runs, int) or runs <= 0:
        raise ValueError("runs must be a positive integer")
    if not isinstance(steps, int) or steps <= 0:
        raise ValueError("steps must be a positive integer")

    start_price = float(start_price)
    if start_price <= 0:
        raise ValueError("start_price must be > 0")

    mu, sigma = float(mu), float(sigma)
    if sigma < 0:
        raise ValueError("sigma must be >= 0")

    rng = random.Random(seed)
    dt = 1.0 / steps
    drift = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * math.sqrt(dt)

    final_prices = []
    drawdowns = []       # deepest fall from a running peak, per path
    troughs = []         # lowest price reached, per path
    sample_paths = []

    for i in range(runs):
        price = peak = trough = start_price
        worst_drawdown = 0.0
        path = [price] if i < keep_paths else None

        for _ in range(steps):
            price *= math.exp(drift + diffusion * rng.gauss(0, 1))
            if path is not None:
                path.append(price)
            if price > peak:
                peak = price
            elif price < trough:
                trough = price
            # Measured against the running peak, so it is the loss someone
            # holding through this path would actually have watched.
            fall = (peak - price) / peak
            if fall > worst_drawdown:
                worst_drawdown = fall

        final_prices.append(price)
        drawdowns.append(worst_drawdown)
        troughs.append(trough)
        if path is not None:
            sample_paths.append(path)

    n = len(final_prices)
    ordered = sorted(final_prices)
    ordered_dd = sorted(drawdowns)

    returns = [(p - start_price) / start_price for p in final_prices]
    avg_return = sum(returns) / n

    # Population standard deviation: this is the whole simulated set, not a
    # sample drawn from something larger.
    variance = sum((r - avg_return) ** 2 for r in returns) / n
    stdev = math.sqrt(variance)

    p5 = _percentile(ordered, 0.05)

    # Expected shortfall: the average outcome once you are already in the worst
    # five percent. The fifth percentile says where the tail begins, this says
    # how bad it is inside it, and they can differ by a lot.
    tail_size = max(1, int(n * 0.05))
    cvar_price = sum(ordered[:tail_size]) / tail_size

    below = lambda frac: sum(1 for p in final_prices if p < start_price * frac) / n

    return {
        "runs": runs, "steps": steps, "mu": mu, "sigma": sigma,
        "start_price": start_price, "seed": seed,

        "avg_final": sum(final_prices) / n,
        "min_final": ordered[0],
        "max_final": ordered[-1],

        "avg_return": avg_return,
        "min_return": (ordered[0] - start_price) / start_price,
        "max_return": (ordered[-1] - start_price) / start_price,
        "return_stdev": stdev,

        "p5": p5,
        "p25": _percentile(ordered, 0.25),
        "p50": _percentile(ordered, 0.50),
        "p75": _percentile(ordered, 0.75),
        "p95": _percentile(ordered, 0.95),

        "cvar5_price": cvar_price,
        "cvar5_return": (cvar_price - start_price) / start_price,

        "prob_loss": below(1.0),
        "prob_loss_20": below(0.8),
        "prob_loss_50": below(0.5),
        "prob_gain_50": sum(1 for p in final_prices if p > start_price * 1.5) / n,

        # What the holder lives through, as opposed to where they end up.
        "median_drawdown": _percentile(ordered_dd, 0.50),
        "worst_drawdown": ordered_dd[-1],
        "p95_drawdown": _percentile(ordered_dd, 0.95),
        "prob_drawdown_20": sum(1 for d in drawdowns if d >= 0.20) / n,
        "prob_drawdown_50": sum(1 for d in drawdowns if d >= 0.50) / n,
        "median_trough": _percentile(sorted(troughs), 0.50),

        "final_prices": final_prices,
        "sample_paths": sample_paths,
    }


def summary_markdown(s: dict) -> str:
    """The figures, laid out for the report. Every number comes from `simulate`."""
    pct = lambda v: f"{v * 100:.2f}%"
    return f"""Parameters:
• Runs: {s['runs']}
• Steps: {s['steps']}
• Mu (drift over the full horizon): {s['mu']}
• Sigma (volatility over the full horizon): {s['sigma']}
• Start Price: {s['start_price']}
• Seed: {s['seed'] if s['seed'] is not None else 'not set, results vary per run'}

Final Price Results:
• Average Final Price: {s['avg_final']:.4f}
• Min Final Price: {s['min_final']:.4f}
• Max Final Price: {s['max_final']:.4f}

Return Results:
• Average Return: {pct(s['avg_return'])}
• Worst Return: {pct(s['min_return'])}
• Best Return: {pct(s['max_return'])}
• Standard Deviation of Returns: {pct(s['return_stdev'])}

Distribution Percentiles:
• P5: {s['p5']:.4f}
• P25: {s['p25']:.4f}
• P50 (Median): {s['p50']:.4f}
• P75: {s['p75']:.4f}
• P95: {s['p95']:.4f}

Tail Risk:
• Value at Risk (5%): {s['p5']:.4f}, a return of {pct((s['p5'] - s['start_price']) / s['start_price'])}
• Expected Shortfall (worst 5%): {s['cvar5_price']:.4f}, a return of {pct(s['cvar5_return'])}

Drawdown, the fall from peak along the way:
• Median Maximum Drawdown: {pct(s['median_drawdown'])}
• 95th Percentile Drawdown: {pct(s['p95_drawdown'])}
• Deepest Drawdown Observed: {pct(s['worst_drawdown'])}
• Probability of a 20% Drawdown: {pct(s['prob_drawdown_20'])}
• Probability of a 50% Drawdown: {pct(s['prob_drawdown_50'])}

Probability Metrics:
• Probability of Loss: {pct(s['prob_loss'])}
• Probability of >20% Loss: {pct(s['prob_loss_20'])}
• Probability of >50% Loss: {pct(s['prob_loss_50'])}
• Probability of >50% Gain: {pct(s['prob_gain_50'])}
"""
