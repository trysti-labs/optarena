package com.optarena;

import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;

public class AppTest {
    @Test
    public void addsTwoNumbers() {
        assertEquals(4, App.add(2, 2));
    }
}
