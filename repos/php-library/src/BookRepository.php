<?php

namespace App;

require_once __DIR__ . '/Book.php';

class BookRepository
{
    /** @var array<int, Book> */
    private array $books = [];
    private int $nextId = 1;

    public function create(string $title, string $author, int $totalCopies): Book
    {
        $book = new Book($this->nextId, $title, $author, $totalCopies);
        $this->books[$book->id] = $book;
        $this->nextId++;
        return $book;
    }

    public function find(int $id): ?Book
    {
        return $this->books[$id] ?? null;
    }

    /** @return Book[] */
    public function all(): array
    {
        $books = array_values($this->books);
        usort($books, fn(Book $a, Book $b) => $a->id <=> $b->id);
        return $books;
    }
}
