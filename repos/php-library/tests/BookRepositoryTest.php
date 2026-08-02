<?php

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../src/BookRepository.php';

final class BookRepositoryTest extends TestCase
{
    public function testCreateAndFind(): void
    {
        $repo = new \App\BookRepository();
        $book = $repo->create('Dune', 'Herbert', 3);
        $this->assertSame('Dune', $book->title);
        $this->assertSame(3, $book->availableCopies);
        $this->assertSame($book, $repo->find($book->id));
    }

    public function testAllSortedById(): void
    {
        $repo = new \App\BookRepository();
        $repo->create('B', 'x', 1);
        $repo->create('A', 'y', 1);
        $ids = array_map(fn($b) => $b->id, $repo->all());
        $sorted = $ids;
        sort($sorted);
        $this->assertSame($sorted, $ids);
    }
}
