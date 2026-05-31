from __future__ import annotations

import logging
from collections import defaultdict


def resolve_dependencies(
    tasks: list[dict],
    all_programs: dict[str, list[dict]],
) -> list[dict]:
    all_by_name: dict[str, dict] = {}
    for progs in all_programs.values():
        for p in progs:
            all_by_name[p["name"]] = p

    task_names: set[str] = {t["name"] for t in tasks}
    task_by_name: dict[str, dict] = {t["name"]: t for t in tasks}

    queue = list(tasks)
    while queue:
        task = queue.pop()
        for dep_name in task.get("depends_on", []):
            if dep_name not in task_names and dep_name in all_by_name:
                dep = dict(all_by_name[dep_name])
                task_names.add(dep_name)
                task_by_name[dep_name] = dep
                queue.append(dep)

    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = defaultdict(int)

    for name in task_names:
        graph.setdefault(name, [])
        in_degree.setdefault(name, 0)

    for name in task_names:
        task = task_by_name[name]
        for dep_name in task.get("depends_on", []):
            if dep_name in task_names:
                graph[dep_name].append(name)
                in_degree[name] += 1

    queue_kahn: list[str] = [n for n in task_names if in_degree[n] == 0]
    sorted_names: list[str] = []

    while queue_kahn:
        node = queue_kahn.pop(0)
        sorted_names.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue_kahn.append(neighbor)

    if len(sorted_names) != len(task_names):
        cycle_nodes = task_names - set(sorted_names)
        logging.warning(
            f"Обнаружен цикл зависимостей: {cycle_nodes}. Порядок не изменён."
        )
        return list(task_by_name.values())

    return [task_by_name[name] for name in sorted_names]


def topological_levels(
    tasks: list[dict],
    all_programs: dict[str, list[dict]],
) -> list[list[dict]]:
    sorted_tasks = resolve_dependencies(tasks, all_programs)

    task_names = {t["name"] for t in sorted_tasks}
    task_by_name = {t["name"]: t for t in sorted_tasks}

    graph: dict[str, list[str]] = {n: [] for n in task_names}
    in_degree: dict[str, int] = {n: 0 for n in task_names}

    for name in task_names:
        for dep_name in task_by_name[name].get("depends_on", []):
            if dep_name in task_names:
                graph[dep_name].append(name)
                in_degree[name] += 1

    levels: list[list[dict]] = []
    current: list[str] = [n for n in task_names if in_degree[n] == 0]

    while current:
        levels.append([task_by_name[n] for n in current])
        next_level: list[str] = []
        for node in current:
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_level.append(neighbor)
        current = next_level

    placed = sum(len(lvl) for lvl in levels)
    if placed != len(task_names):
        logging.warning(
            "Цикл зависимостей при разбиении на уровни, fallback к одному уровню"
        )
        return [sorted_tasks]

    return levels
