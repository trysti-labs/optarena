package com.optarena.tasktracker.repository;

import java.util.ArrayList;
import java.util.List;

final class Paging {
    private Paging() {
    }

    static <T> List<T> page(List<T> items, int skip, int limit) {
        if (skip >= items.size()) {
            return List.of();
        }
        int end = Math.min(items.size(), skip + limit);
        return new ArrayList<>(items.subList(skip, end));
    }
}
